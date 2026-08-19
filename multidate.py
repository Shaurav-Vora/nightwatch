#!/usr/bin/env python3
"""Does the finding survive on other days?

The report's own Limitations section says "modelled temperature field for a
single day". That is the first thing a reviewer will attack, and rightly:
one hot afternoon in Phoenix proves nothing about whether exposure is a
stable property of a place or an accident of that day's weather.

This runs the whole measurement again on four more summer dates per city and
asks two questions:

  1. AMPLIFICATION -- is the degrees-to-hours gap consistent, or was
     2025-07-22 unusual? Reported as the spread across dates, not an average
     that hides it.

  2. SITE STABILITY -- the stronger test. Do the SAME neighbourhoods rank
     worst each time? If the top site moves kilometres between dates, the
     ranking is weather and the export is worthless for planning. If it
     lands in the same place on five independent heat events, exposure is a
     property of the built environment and the ranking is actionable.

Cost: 3 calls per city-date. 3 cities x 4 new dates = 36 calls = 151,920
credits. The existing date is free (cached).

    python multidate.py                 # all cities
    python multidate.py --city phoenix
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from city_test import CITIES  # noqa: E402
from duration_test import THRESHOLDS  # noqa: E402
from amplification import ols1  # noqa: E402

GRAN, DAY_HOUR, NIGHT_HOUR = 100, 15, 3
WATER_COOLING_MAX = 5.0
OUT = Path("web/data/multidate.json")

# Summer dates spread across four years, so the answer cannot be an artefact
# of one season or one synoptic pattern. All inside the API's supported range.
DATES = ["2022-07-19", "2023-07-25", "2024-07-09", "2024-08-06", "2025-07-22"]


def fetch_tcm(client, ring, date, hour):
    r = client.heatmap(geo.feature_collection(ring), date, filter_type=1,
                       start_time=f"{hour:02d}:00", granularity=GRAN,
                       analytic_type="tcm")
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        t = p.get("average_temperature")
        if t is None:
            continue
        g = (f.get("geometry", {}).get("coordinates") or [[]])[0]
        if not g:
            continue
        out[p.get("tile_id")] = (float(t),
                                 sum(c[0] for c in g) / len(g),
                                 sum(c[1] for c in g) / len(g))
    return out


def fetch_persistence(client, ring, date, threshold):
    r = client.heatmap(geo.feature_collection(ring), date, filter_type=3,
                       granularity=GRAN, analytic_type="persistence",
                       threshold=threshold, direction="above")
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        if p.get("value") is not None:
            out[p.get("tile_id")] = max(0.0, float(p["value"]))
    return out


def top_site(cells, margin=1.0):
    """Centroid of the blocks meaningfully worse than the city median.

    Anchored to the median rather than a fixed top-N fraction, and for the
    same reason the app's site detection is: a fraction larger than the
    genuinely hot area pads itself with arbitrary median blocks, which drags
    the centroid and makes the stability test measure the sampling rule
    instead of the city. Falls back to the worst 2% only if nothing clears
    the margin, so a flat city still returns something rather than crashing.
    """
    e = [c["e"] for c in cells]
    med = statistics.median(e)
    hot = [c for c in cells if c["e"] >= med + margin]
    if len(hot) < 10:
        hot = sorted(cells, key=lambda c: -c["e"])[:max(10, len(cells) // 50)]
    return (statistics.mean(c["lon"] for c in hot),
            statistics.mean(c["lat"] for c in hot))


def km(a, b):
    (lo1, la1), (lo2, la2) = a, b
    kx = 111.32 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot((lo1 - lo2) * kx, (la1 - la2) * 111.32)


def run_city(client, key):
    cfg, th = CITIES[key], THRESHOLDS[key]
    print(f"\n{'=' * 74}\n{cfg['label']}   threshold {th}C\n{'=' * 74}")
    rows = []

    for date in DATES:
        try:
            day = fetch_tcm(client, cfg["ring"], date, DAY_HOUR)
            night = fetch_tcm(client, cfg["ring"], date, NIGHT_HOUR)
            expo = fetch_persistence(client, cfg["ring"], date, th)
        except Exception as e:
            print(f"  {date}  FAILED  {type(e).__name__}: {str(e)[:70]}")
            continue

        ids = sorted(set(day) & set(night) & set(expo))
        cells = [{"lon": day[i][1], "lat": day[i][2], "d": day[i][0],
                  "n": night[i][0], "e": expo[i]} for i in ids
                 if day[i][0] - night[i][0] > WATER_COOLING_MAX]
        if len(cells) < 200:
            print(f"  {date}  only {len(cells)} land tiles; skipped")
            continue

        d = [c["d"] for c in cells]
        e = [c["e"] for c in cells]
        slope, _, r2 = ols1(d, e)
        rows.append({
            "date": date, "n": len(cells),
            "temp_span": round(max(d) - min(d), 2),
            "expo_span": round(max(e) - min(e), 2),
            "expo_mean": round(statistics.mean(e), 2),
            "day_mean": round(statistics.mean(d), 2),
            "r2": round(r2, 3), "slope": round(slope, 2),
            "top": top_site(cells),
        })
        r = rows[-1]
        print(f"  {date}  {r['n']:6d} tiles   "
              f"temp {r['temp_span']:5.2f}C -> exposure {r['expo_span']:5.2f}h"
              f"   mean {r['expo_mean']:5.2f}h   R2 {r['r2']:.3f}")

    if len(rows) < 2:
        print("  not enough dates succeeded to judge stability")
        return None

    # --- consistency of the headline -------------------------------
    ratios = [r["expo_span"] / r["temp_span"] for r in rows if r["temp_span"]]
    print(f"\n  amplification (hours of exposure per degree of temperature span)")
    print(f"    {min(ratios):.2f} to {max(ratios):.2f}   "
          f"median {statistics.median(ratios):.2f}")

    # --- the stronger test -----------------------------------------
    tops = [r["top"] for r in rows]
    dists = [km(a, b) for i, a in enumerate(tops) for b in tops[i + 1:]]
    print(f"\n  worst-exposure centroid, across {len(rows)} dates")
    for r in rows:
        print(f"    {r['date']}   {r['top'][1]:.4f}, {r['top'][0]:.4f}")
    print(f"    pairwise distance: mean {statistics.mean(dists):.2f} km, "
          f"max {max(dists):.2f} km")
    if max(dists) < 2.0:
        print("    => the same ground every time. Exposure is a property of")
        print("       the place, not of the day. The ranking is actionable.")
    elif max(dists) > 6.0:
        print("    => the worst area MOVES between dates. The ranking is")
        print("       partly weather; say so and do not sell the export as a")
        print("       fixed list of sites.")
    else:
        print("    => broadly stable with some drift. Quote the distance.")

    return {"city": key, "label": cfg["label"], "threshold_c": th,
            "dates": rows,
            "amp_min": round(min(ratios), 2), "amp_max": round(max(ratios), 2),
            "amp_median": round(statistics.median(ratios), 2),
            "centroid_max_km": round(max(dists), 2),
            "centroid_mean_km": round(statistics.mean(dists), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="all", choices=list(CITIES) + ["all"])
    args = ap.parse_args()
    load_dotenv()
    client = NightwatchClient()

    try:
        before = client.usage()["credit_summary"]["cycle_remaining_credits"]
    except Exception:
        before = None

    keys = list(CITIES) if args.city == "all" else [args.city]
    out = [r for r in (run_city(client, k) for k in keys) if r]

    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    print(f"  {'city':<9} {'dates':>6} {'amplification h/C':>19} "
          f"{'centroid drift':>16}")
    for r in out:
        print(f"  {r['city']:<9} {len(r['dates']):6d} "
              f"{r['amp_min']:7.2f} – {r['amp_max']:<7.2f} "
              f"{r['centroid_max_km']:11.2f} km max")

    if out:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"dates": DATES, "cities": out}, indent=2))
        print(f"\n  wrote {OUT}")
        stable = [r for r in out if r["centroid_max_km"] < 2.0]
        if len(stable) == len(out):
            print("\n  Every city puts its worst exposure on the same ground")
            print("  across all dates. This is the answer to 'it's only one")
            print("  day' -- put it in the report.")

    if before is not None:
        try:
            after = client.usage()["credit_summary"]["cycle_remaining_credits"]
            print(f"\n  credits {before:,} -> {after:,} "
                  f"(spent {before - after:,})")
        except Exception:
            pass


if __name__ == "__main__":
    main()
