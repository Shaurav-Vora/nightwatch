#!/usr/bin/env python3
"""Why is the persistent core persistent? Land cover, and a photo.

Houston has 1,408 blocks in the worst exposure quartile on all five heat
events measured -- 64.6x what chance would produce. That establishes THAT
they are consistently worst. It says nothing about WHY.

This samples those blocks against blocks that were never in the worst
quartile, runs FortyGuard's satellite segmentation on both, and compares
land cover. If the persistent core is materially more impervious and less
vegetated, the result stops being a statistical curiosity and becomes a
statement about the built environment -- which is what makes it actionable.

Then it pulls street view at the core's centroid, so the report has a
photograph of the place rather than only a polygon.

Two sampling problems this has to avoid
---------------------------------------
The always-worst blocks are spatially clustered -- that is what "core"
means. Sampling both groups naively would compare one neighbourhood against
another and attribute every difference between them to exposure. So:

  * both groups are sampled by FARTHEST-POINT selection, which spreads the
    picks across the full extent of each group rather than letting them
    bunch in whichever corner the iteration happened to start.

  * the never-worst sample is restricted to blocks within the same distance
    band from the city centre as the core, so the comparison is not simply
    inner city against outskirts.

Neither fully removes the confound -- with n=15 per group nothing does --
and the output says so.

Cost: 2*N satellite calls at 14,400 each (default N=15 -> 432,000) plus 4
street view calls at 8,600 (34,400). It prints the bill and waits for
confirmation before spending anything.

    python explain_core.py --city houston
    python explain_core.py --city houston --n 10 --yes
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch.client import NightwatchClient  # noqa: E402
from city_test import CITIES  # noqa: E402
from multidate import km  # noqa: E402

DATA = Path("web/data")
SAT_COST, SV_COST = 14_400, 8_600
HEADINGS = [0, 90, 180, 270]


def farthest_point_sample(pts, n, seed=0):
    """Pick n points spread across the extent, not clustered.

    Greedy: start from the point nearest the group's centroid, then
    repeatedly take whichever remaining point is farthest from everything
    already chosen. Deterministic given the seed, and it cannot return a
    tight cluster the way random sampling can.
    """
    if len(pts) <= n:
        return list(pts)
    cx = statistics.mean(p[0] for p in pts)
    cy = statistics.mean(p[1] for p in pts)
    chosen = [min(pts, key=lambda p: km((cx, cy), (p[0], p[1])))]
    remaining = [p for p in pts if p is not chosen[0]]
    while len(chosen) < n and remaining:
        best, bd = None, -1.0
        for p in remaining:
            d = min(km((p[0], p[1]), (c[0], c[1])) for c in chosen)
            if d > bd:
                best, bd = p, d
        chosen.append(best)
        remaining.remove(best)
    return chosen


def permutation_diff(a, b, iters=20000, seed=1):
    """Two-sided p for the difference in means, without scipy.

    With n=15 a t-test's normality assumption is doing real work; shuffling
    the labels assumes nothing.
    """
    if not a or not b:
        return float("nan"), float("nan")
    obs = statistics.mean(a) - statistics.mean(b)
    pool = list(a) + list(b)
    na = len(a)
    rnd = random.Random(seed)
    hits = 0
    for _ in range(iters):
        rnd.shuffle(pool)
        d = statistics.mean(pool[:na]) - statistics.mean(pool[na:])
        if abs(d) >= abs(obs):
            hits += 1
    return obs, (hits + 1) / (iters + 1)


def segment(client, lat, lon, date):
    r = client.satellite(lat, lon, date, start_time="14:00", granularity=80)
    seg = ((r or {}).get("segmentation") or {})
    return seg.get("segments") or {}, (r or {}).get("image_year")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="houston", choices=list(CITIES))
    ap.add_argument("--n", type=int, default=15, help="blocks per group")
    ap.add_argument("--skip-streetview", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    args = ap.parse_args()

    load_dotenv()
    cfg = CITIES[args.city]
    cons_path = DATA / f"consistency_{args.city}.json"
    if not cons_path.exists():
        sys.exit(f"{cons_path} missing — run consistency.py first")
    cons = json.loads(cons_path.read_text())

    always = [c for c in cons["cells"] if c[2] >= 0.999]
    never = [c for c in cons["cells"] if c[2] <= 0.001]
    print(f"{cfg['label']}")
    print(f"  {len(always)} always-worst blocks, {len(never)} never-worst, "
          f"over {len(cons['dates'])} heat events")
    if len(always) < args.n or len(never) < args.n:
        sys.exit("not enough blocks in one of the groups")

    # keep the never-worst sample in the same distance band from the core's
    # centre, so this is not simply inner city vs outskirts
    cx = statistics.mean(c[0] for c in always)
    cy = statistics.mean(c[1] for c in always)
    dists = sorted(km((cx, cy), (c[0], c[1])) for c in always)
    band = dists[int(len(dists) * 0.9)]
    near = [c for c in never if km((cx, cy), (c[0], c[1])) <= band * 1.6]
    print(f"  core centred {cy:.4f}, {cx:.4f}; 90% of it within {band:.1f} km")
    print(f"  {len(near)} never-worst blocks lie inside {band*1.6:.1f} km "
          f"of that centre and are eligible as controls")
    if len(near) < args.n:
        print("  ! too few nearby controls; falling back to the full set,")
        print("    which weakens the comparison — say so in the write-up")
        near = never

    sa = farthest_point_sample(always, args.n)
    sb = farthest_point_sample(near, args.n)

    n_sat = len(sa) + len(sb)
    n_sv = 0 if args.skip_streetview else len(HEADINGS)
    bill = n_sat * SAT_COST + n_sv * SV_COST
    print(f"\n  {n_sat} satellite calls x {SAT_COST:,} = {n_sat*SAT_COST:,}")
    if n_sv:
        print(f"  {n_sv} street view calls x {SV_COST:,} = {n_sv*SV_COST:,}")
    print(f"  TOTAL {bill:,} credits")
    if not args.yes and input("  proceed? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled — nothing spent")

    client = NightwatchClient()
    date = cfg["date"]

    groups = {}
    for name, sample in (("always_worst", sa), ("never_worst", sb)):
        rows, years = [], []
        print(f"\n  segmenting {name} ({len(sample)} blocks)")
        for i, c in enumerate(sample, 1):
            try:
                segs, yr = segment(client, c[1], c[0], date)
            except Exception as e:
                print(f"    {i:2d}. failed: {type(e).__name__}: {str(e)[:60]}")
                continue
            if not segs:
                print(f"    {i:2d}. empty segmentation")
                continue
            rows.append(segs)
            if yr:
                years.append(yr)
            top = max(segs, key=segs.get)
            print(f"    {i:2d}. {c[1]:.4f},{c[0]:.4f}  "
                  f"{top} {segs[top]:.0f}%  ({len(segs)} classes)")
        groups[name] = {"rows": rows, "years": years, "sample": sample}

    # --- compare ----------------------------------------------------
    classes = sorted({k for g in groups.values() for r in g["rows"] for k in r})
    print("\n" + "=" * 74)
    print("LAND COVER: persistent core vs never-worst")
    print("=" * 74)
    print(f"  {'class':<22} {'core':>8} {'control':>9} {'diff':>8} {'p':>8}")
    comparison = []
    for cls in classes:
        a = [r.get(cls, 0.0) for r in groups["always_worst"]["rows"]]
        b = [r.get(cls, 0.0) for r in groups["never_worst"]["rows"]]
        if not a or not b:
            continue
        diff, p = permutation_diff(a, b)
        ma, mb = statistics.mean(a), statistics.mean(b)
        if max(ma, mb) < 1.0:          # ignore trace classes
            continue
        star = "  <<<" if p < 0.05 else ""
        print(f"  {cls:<22} {ma:7.1f}% {mb:8.1f}% {diff:+7.1f} {p:8.3f}{star}")
        comparison.append({"class": cls, "core_pct": round(ma, 2),
                           "control_pct": round(mb, 2),
                           "diff_pct": round(diff, 2), "p": round(p, 4)})

    sig = [c for c in comparison if c["p"] < 0.05]
    print(f"\n  {len(sig)} of {len(comparison)} classes differ at p<0.05 "
          f"(permutation test, n={args.n} per group)")
    if sig:
        top = max(sig, key=lambda c: abs(c["diff_pct"]))
        print(f"  largest: {top['class']} {top['diff_pct']:+.1f} points "
              f"({top['core_pct']:.0f}% vs {top['control_pct']:.0f}%)")
    else:
        print("  No class separates the groups. The core is not explained by")
        print("  land cover at this sample size — report that rather than")
        print("  hunting for a story.")

    # --- street view -------------------------------------------------
    sv = []
    if not args.skip_streetview:
        print(f"\n  street view at the core centre ({cy:.4f}, {cx:.4f})")
        for hdg in HEADINGS:
            try:
                r = client.streetview(cy, cx, horizontal_angle=float(hdg),
                                      vertical_angle=15.0)
            except Exception as e:
                print(f"    {hdg:3d}deg failed: {type(e).__name__}")
                continue
            front = (r or {}).get("front") or {}
            entry = {"heading": hdg, "segments": front.get("segments") or {},
                     "image_date": front.get("image_date")}
            for key, tag in (("original_image", "photo"),
                             ("segmented_image", "mask")):
                b64 = front.get(key)
                if not b64:
                    continue
                raw = b64.split(",")[-1]
                try:
                    blob = base64.b64decode(raw)
                except Exception:
                    continue
                fn = DATA / f"sv_{args.city}_{hdg}_{tag}.png"
                fn.write_bytes(blob)
                entry[tag] = fn.name
                print(f"    {hdg:3d}deg -> {fn.name} ({len(blob)/1024:.0f} KB)")
            sv.append(entry)

    out = {
        "city": args.city, "label": cfg["label"], "date": date,
        "n_per_group": args.n,
        "n_always_total": len(always), "n_never_total": len(never),
        "core_centre": [round(cx, 5), round(cy, 5)],
        "core_radius_km": round(band, 2),
        "imagery_years": sorted({y for g in groups.values() for y in g["years"]}),
        "comparison": comparison,
        "n_significant": len(sig),
        "streetview": sv,
        "caveat": (f"n={args.n} blocks per group, sampled by farthest-point "
                   f"selection so neither group is a single cluster. Controls "
                   f"are restricted to the same distance band from the core "
                   f"centre. Land cover and exposure are still observational: "
                   f"this shows what the persistent core is made of, not that "
                   f"the material causes the exposure."),
    }
    (DATA / f"explain_{args.city}.json").write_text(json.dumps(out, indent=2))
    print(f"\n  wrote {DATA / f'explain_{args.city}.json'}")

    try:
        rem = client.usage()["credit_summary"]["cycle_remaining_credits"]
        print(f"  credits remaining: {rem:,}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
