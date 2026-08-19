#!/usr/bin/env python3
"""Step 10: is the flat field a granularity artefact? (60 m costs the same)

The finding that prompted this
------------------------------
The usage breakdown gives exact per-call costs:

    Heatmap Generation     78 calls   329,160 credits  ->  4,220 each
    Satellite Segmentation  6 calls    86,400 credits  -> 14,400 each
    Streetview              1 call      8,600 credits  ->  8,600 each
    Environment Parameters  2 calls      5,800 credits  ->  2,900 each

329,160 / 78 = exactly 4,220. Cost is a FLAT RATE PER CALL. A 0.6 mi^2
request costs the same as an 83 mi^2 one, and 60 m granularity costs the same
as 100 m. Everything computed earlier on a "credits per tile" model was wrong.

Which raises the obvious question. We have spent all day concluding the
temperature field is too smooth to map -- Phoenix showed 0.365C of daytime
spread across 83 mi^2, which is not credible as physics for a city with that
urban heat island. But every one of those calls was at 100 m, the COARSEST
setting available, when the finest was free.

If 60 m reveals structure that 100 m averaged away, the whole "flat field"
conclusion was an artefact of a setting we had no reason to choose.

What this measures
------------------
Same AOI, same date, same analytic, two granularities. Compares spread and
-- more importantly -- LOCAL TEXTURE: whether neighbouring tiles differ.
A field that is merely rescaled will show similar sd; a field that resolves
more will show materially higher tile-to-tile variation.

Cost: 4 calls = 16,880 credits.

    python granularity_test.py
    python granularity_test.py --city houston --analytic persistence
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from city_test import CITIES  # noqa: E402
from duration_test import THRESHOLDS  # noqa: E402


def fetch(client, ring, date, gran, analytic, threshold, hour=None):
    kw = {}
    if analytic in ("exceedance", "persistence"):
        kw = {"threshold": threshold, "direction": "above"}
        ft, st = 3, None
    else:
        ft, st = 1, f"{hour:02d}:00"

    r = client.heatmap(
        geo.feature_collection(ring), date, filter_type=ft,
        start_time=st, granularity=gran, analytic_type=analytic, **kw,
    )
    tiles = []
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        v = p.get("value")
        if v is None:
            v = p.get("average_temperature")
        if v is None:
            continue
        g = (f.get("geometry", {}).get("coordinates") or [[]])[0]
        if not g:
            continue
        tiles.append((
            sum(c[0] for c in g) / len(g),
            sum(c[1] for c in g) / len(g),
            float(v),
        ))
    return tiles


def texture(tiles):
    """Mean absolute difference between horizontally adjacent tiles.

    This is the number that distinguishes 'more pixels' from 'more
    information'. A field that is merely resampled finer will have very
    similar neighbours; a field that genuinely resolves smaller features
    will not.
    """
    if len(tiles) < 50:
        return float("nan"), float("nan")
    lons = sorted({round(t[0], 5) for t in tiles})
    steps = [b - a for a, b in zip(lons, lons[1:]) if b - a > 1e-7]
    if not steps:
        return float("nan"), float("nan")
    step = min(steps)
    grid = {(round(t[0], 5), round(t[1], 5)): t[2] for t in tiles}

    diffs = []
    for (lo, la), v in grid.items():
        nb = grid.get((round(lo + step, 5), la))
        if nb is not None:
            diffs.append(abs(v - nb))
    if not diffs:
        return float("nan"), float("nan")

    vals = [t[2] for t in tiles]
    rng = (max(vals) - min(vals)) or 1.0
    return statistics.mean(diffs), statistics.mean(diffs) / rng


def report(label, tiles):
    vals = sorted(t[2] for t in tiles)
    n = len(vals)
    tex, rel = texture(tiles)
    d = {
        "n": n,
        "sd": statistics.pstdev(vals),
        "span": vals[-1] - vals[0],
        "iqr": vals[n * 3 // 4] - vals[n // 4],
        "texture": tex,
        "texture_rel": rel,
    }
    print(f"  {label:<12} n={n:6d}  sd {d['sd']:6.3f}  span {d['span']:6.2f}  "
          f"IQR {d['iqr']:5.2f}  neighbour-diff {tex:6.4f}")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="phoenix", choices=list(CITIES))
    ap.add_argument("--analytic", default="both",
                    choices=["tcm", "persistence", "both"])
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient()
    cfg = CITIES[args.city]
    th = THRESHOLDS[args.city]

    # A smaller AOI so 60 m does not return an unwieldy payload; area is free
    # but the JSON still has to be parsed. ~20 mi^2 keeps it manageable.
    w, s, e, n = geo.bbox_of(cfg["ring"])
    ring = geo.bbox_ring(w, s, w + (e - w) * 0.5, s + (n - s) * 0.5)

    print("=" * 78)
    print(f"GRANULARITY TEST — {cfg['label']}")
    print(f"{geo.polygon_area_sq_mi(ring):.1f} mi^2  ·  {cfg['date']}  ·  "
          f"4,220 credits per call regardless of granularity")
    print("=" * 78)

    analytics = (["tcm", "persistence"] if args.analytic == "both"
                 else [args.analytic])

    for analytic in analytics:
        print(f"\n{analytic.upper()}"
              + (f"  (threshold {th}C)" if analytic == "persistence" else
                 "  (15:00 local)"))
        out = {}
        for gran in (100, 60):
            try:
                tiles = fetch(client, ring, cfg["date"], gran, analytic,
                              th, hour=15)
            except Exception as ex:
                print(f"  {gran} m: FAILED {type(ex).__name__}: {ex}")
                continue
            out[gran] = report(f"{gran} m", tiles)

        if 100 in out and 60 in out:
            a, b = out[100], out[60]
            print(f"\n    tiles      x{b['n']/a['n']:.2f}   "
                  f"(expected x2.78 if the grid simply gets finer)")
            print(f"    sd         x{b['sd']/a['sd']:.2f}" if a['sd'] else "")
            print(f"    span       x{b['span']/a['span']:.2f}" if a['span'] else "")
            if a["texture"] == a["texture"] and b["texture"] == b["texture"]:
                print(f"    neighbour-diff  {a['texture']:.4f} -> "
                      f"{b['texture']:.4f}  "
                      f"(x{b['texture']/a['texture']:.2f})"
                      if a["texture"] else "")

            gain = (b["sd"] / a["sd"]) if a["sd"] else float("nan")
            print()
            if gain > 1.25:
                print("    => 60 m RESOLVES MORE. The flat field was partly a")
                print("       granularity artefact. Re-harvest everything at")
                print("       60 m -- it is free and we had no reason to be")
                print("       using the coarsest setting.")
            elif gain < 1.05:
                print("    => 60 m adds pixels, not information. The field is")
                print("       genuinely smooth at this scale, so the earlier")
                print("       conclusion stands and 100 m is fine. Cheaper to")
                print("       render, too.")
            else:
                print("    => Marginal gain. Use 60 m anyway since it is free,")
                print("       but do not expect it to rescue anything.")

    print("\n" + "=" * 78)
    print("BUDGET, now that cost is understood")
    print("=" * 78)
    try:
        u = client.usage()["credit_summary"]
        rem = u["cycle_remaining_credits"]
        print(f"  remaining {rem:,} credits = {rem // 4220} more heatmap calls")
        print(f"  a 3-city harvest at 60 m, 3 analytics each = 9 calls "
              f"= {9 * 4220:,} credits ({100 * 9 * 4220 / rem:.1f}% of what is left)")
    except Exception as ex:
        print(f"  usage lookup failed: {ex}")


if __name__ == "__main__":
    main()
