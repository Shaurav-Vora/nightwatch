#!/usr/bin/env python3
"""Which blocks are ALWAYS bad, rather than bad on one day?

Zero credits -- every date is already cached by multidate.py.

Why this replaces the centroid test
-----------------------------------
multidate.py asked whether the centroid of the worst 10% lands in the same
place each date. It does not: 8.4 km of drift in Phoenix, 13.2 km in
Houston. But that statistic was the wrong one, for two reasons the same run
exposed:

  * A fixed threshold saturates. Phoenix on 2023-07-25 had a temperature
    span of 0.43 C and mean exposure of 20.4 of 24 hours -- nearly the whole
    city above 35 C nearly all day. When almost everything qualifies, "the
    worst 10%" is close to arbitrary and its centroid is noise.

  * A centroid is dominated by one mobile tail. It cannot distinguish "the
    same blocks are worst, plus some day-specific extras" from "entirely
    different blocks are worst".

The per-block question avoids both. For each date, rank blocks WITHIN that
date -- so saturation and threshold choice cancel out, because the ranking
is relative to that day's own distribution -- then count how often each
block lands in the worst quartile.

  consistency = (dates in worst quartile) / (dates measured)

1.0 means the block was among the worst on every heat event measured. Those
blocks, if they exist and if they form coherent areas, are what a planner
can act on. Blocks that are only occasionally bad are weather.

Writes web/data/consistency_<city>.json for the map.

    python consistency.py
    python consistency.py --city phoenix --quantile 0.25
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from city_test import CITIES  # noqa: E402
from duration_test import THRESHOLDS  # noqa: E402
from multidate import DATES, fetch_tcm, fetch_persistence, km  # noqa: E402

GRAN, DAY_HOUR, NIGHT_HOUR = 100, 15, 3
WATER_COOLING_MAX = 5.0
# A date is only comparable if it returned a similar amount of land as the
# city's other dates. An absolute floor is not enough: Chicago's 2024-07-09
# returned 1,829 land tiles against 8,176-9,249 on its other dates -- a cool
# day where most of the city never cleared the threshold. It passed a flat
# 200-tile guard, then collapsed the cross-date intersection to that cool-day
# subset and produced a "no persistent core" verdict computed on the wrong
# blocks. Compare each date against the city's own median instead.
MIN_TILES = 200
MIN_FRACTION_OF_MEDIAN = 0.5

# The mirror-image guard. A date can fail to discriminate by being too HOT
# as well as too cool: Phoenix on 2023-07-25 had a worst-quartile cut of
# 23.98 h, meaning a quarter of the city was above threshold for essentially
# the whole day. Every one of those blocks is pinned at the ceiling and
# their ordering is arbitrary, so the date contributes noise to a
# consistency count while still voting on it. Both guards are applied to
# every city, and excluded dates are printed rather than dropped silently.
SATURATED_CUT_H = 23.0
OUT = Path("web/data")


def load_date(client, cfg, th, date):
    """Cached. Returns {tile_id: (exposure, lon, lat)} for land tiles only."""
    day = fetch_tcm(client, cfg["ring"], date, DAY_HOUR)
    night = fetch_tcm(client, cfg["ring"], date, NIGHT_HOUR)
    expo = fetch_persistence(client, cfg["ring"], date, th)
    out = {}
    for i in set(day) & set(night) & set(expo):
        if day[i][0] - night[i][0] > WATER_COOLING_MAX:
            out[i] = (expo[i], day[i][1], day[i][2])
    return out


def run_city(client, key, q):
    cfg, th = CITIES[key], THRESHOLDS[key]
    print(f"\n{'=' * 74}\n{cfg['label']}\n{'=' * 74}")

    loaded = {}
    for date in DATES:
        try:
            loaded[date] = load_date(client, cfg, th, date)
        except Exception as e:
            print(f"  {date}  unavailable ({type(e).__name__})")

    if not loaded:
        print("  no dates available")
        return None

    med_tiles = statistics.median(len(d) for d in loaded.values())
    floor = max(MIN_TILES, med_tiles * MIN_FRACTION_OF_MEDIAN)

    per_date, used, dropped = {}, [], []
    for date, d in loaded.items():
        e = [v[0] for v in d.values()]
        if len(d) < floor:
            print(f"  {date}  {len(d):6d} tiles   "
                  f"mean {statistics.mean(e) if e else 0:5.2f}h   "
                  f"EXCLUDED — {len(d)/med_tiles:.0%} of this city's median "
                  f"{med_tiles:.0f}, too cool to be a comparable heat event")
            dropped.append((date, "too cool"))
            continue
        cut = sorted(e)[int(len(e) * (1 - q))]
        if cut >= SATURATED_CUT_H:
            print(f"  {date}  {len(d):6d} tiles   mean {statistics.mean(e):5.2f}h"
                  f"   EXCLUDED — worst-quartile cut {cut:.2f}h is at the 24h "
                  f"ceiling, so the top quartile cannot be ordered")
            dropped.append((date, "saturated"))
            continue
        per_date[date] = d
        used.append(date)
        print(f"  {date}  {len(d):6d} tiles   mean {statistics.mean(e):5.2f}h   "
              f"worst-quartile cut {cut:5.2f}h")

    if len(used) < 3:
        print(f"  only {len(used)} usable dates — cannot judge consistency")
        return None

    # Rank WITHIN each date, so a hot day and a mild day contribute equally
    # and the absolute threshold stops mattering.
    hits = defaultdict(int)
    seen = defaultdict(int)
    coords = {}
    for date, d in per_date.items():
        cut = sorted(v[0] for v in d.values())[int(len(d) * (1 - q))]
        for tid, (e, lon, lat) in d.items():
            seen[tid] += 1
            coords[tid] = (lon, lat)
            if e >= cut:
                hits[tid] += 1

    # only blocks measured on every usable date
    full = [t for t in seen if seen[t] == len(used)]
    cons = {t: hits[t] / len(used) for t in full}
    always = [t for t in full if cons[t] == 1.0]
    never = [t for t in full if cons[t] == 0.0]

    print(f"\n  {len(full)} blocks measured on all {len(used)} dates")
    dist = defaultdict(int)
    for t in full:
        dist[hits[t]] += 1
    for k in sorted(dist):
        bar = '#' * int(40 * dist[k] / max(dist.values()))
        print(f"    worst quartile on {k}/{len(used)} dates  {bar:<40} {dist[k]}")

    # If blocks were independently random, P(all N) = q^N.
    expected = len(full) * (q ** len(used))
    print(f"\n  blocks in the worst quartile EVERY date: {len(always)}")
    print(f"  expected by chance if it were random:    {expected:.1f}")
    if expected > 0:
        print(f"  ratio: {len(always)/max(expected,1e-9):.1f}x chance")

    verdict = None
    if len(always) >= 30 and len(always) > 4 * expected:
        # are they contiguous, or scattered?
        pts = [coords[t] for t in always]
        cx = statistics.mean(p[0] for p in pts)
        cy = statistics.mean(p[1] for p in pts)
        spread = statistics.median(km((cx, cy), p) for p in pts)
        print(f"  centred {cy:.4f}, {cx:.4f}, median radius {spread:.2f} km")
        verdict = "stable"
        print("\n  => A persistent set of blocks is worst on EVERY heat event.")
        print("     The daily centroid moves, but these do not. This is the")
        print("     defensible site list, and it is a stronger claim than the")
        print("     single-date ranking because it survived four more days.")
    elif len(always) < 5:
        verdict = "none"
        print("\n  => Essentially no block is consistently worst. Exposure")
        print("     geography is weather-driven here. Report the magnitude")
        print("     finding and do not present a fixed site list.")
    else:
        verdict = "weak"
        print("\n  => A small persistent core exists but most of the ranking")
        print("     turns over between dates. Quote both numbers.")

    payload = {
        "city": key, "label": cfg["label"], "threshold_c": th,
        "dates": used, "dates_excluded": dropped, "quantile": q,
        "n_blocks": len(full), "n_always": len(always), "n_never": len(never),
        "expected_by_chance": round(expected, 1),
        "verdict": verdict,
        "cells": [[round(coords[t][0], 5), round(coords[t][1], 5),
                   round(cons[t], 3)] for t in full],
    }
    (OUT / f"consistency_{key}.json").write_text(
        json.dumps(payload, separators=(",", ":")))
    print(f"  wrote {OUT / f'consistency_{key}.json'}")
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="all", choices=list(CITIES) + ["all"])
    ap.add_argument("--quantile", type=float, default=0.25,
                    help="worst fraction counted as 'bad' each date")
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient()
    OUT.mkdir(parents=True, exist_ok=True)

    keys = list(CITIES) if args.city == "all" else [args.city]
    out = [r for r in (run_city(client, k, args.quantile) for k in keys) if r]

    print("\n" + "=" * 74)
    print("SUMMARY — blocks worst on EVERY measured heat event")
    print("=" * 74)
    print(f"  {'city':<9} {'dates':>6} {'blocks':>8} {'always worst':>13} "
          f"{'vs chance':>11}  verdict")
    for r in out:
        ratio = r["n_always"] / max(r["expected_by_chance"], 1e-9)
        print(f"  {r['city']:<9} {len(r['dates']):6d} {r['n_blocks']:8d} "
              f"{r['n_always']:13d} {ratio:10.1f}x  {r['verdict']}")

    if out and all(r["verdict"] == "stable" for r in out):
        print("\n  Every city has a persistent worst-exposure core. The site")
        print("  list is a property of the built environment, not of one day.")


if __name__ == "__main__":
    main()
