#!/usr/bin/env python3
"""Step 8: is the flat night signal a CHICAGO problem or a MODEL problem?

Where we are
------------
Chicago gave a striking day/night reversal (rho -0.43) that evaporated once
tiles within 3 km of Lake Michigan were excluded: rho went to +0.13, i.e. no
reversal at all. And night heat retention -- 3am temperature minus a lon/lat
plane -- had sd of just 0.143C. Whatever urban structure exists there is at
or below the level worth mapping.

Two explanations, and they lead to opposite decisions:

  (a) CHICAGO IS A BAD TEST BED. A 494 km^2 lake pressed against the city
      dominates the night field and leaves nothing for urban form to
      explain. Pick a dry city and the signal appears.

  (b) THE MODEL DOES NOT RESOLVE THIS. FortyGuard's field is too smooth at
      night for block-level structure anywhere, and no city will show it.

Phoenix separates them. No large water body, extreme documented urban heat
island, arid. If night-time urban structure exists in this data at all, it
exists there. If Phoenix is also flat, the answer is (b) and the project
needs rethinking rather than relocating.

Cost: 2 heatmap calls per city (~8,500 credits each at 37 mi^2 / g100).

    python city_test.py                  # phoenix, the decisive one
    python city_test.py --city houston
    python city_test.py --city all
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
from cooling_deficit import spearman  # noqa: E402
from detrend import ols2  # noqa: E402

DAY_HOUR, NIGHT_HOUR, GRAN = 15, 3, 100

# start_time is LOCAL (established in step 3), so no timezone maths here --
# 15:00 means 15:00 where the city is.
CITIES = {
    "phoenix": {
        "label": "Phoenix, AZ — arid, no water, extreme UHI",
        "ring": geo.bbox_ring(-112.16, 33.40, -112.00, 33.53),
        "date": "2025-07-22",
    },
    "houston": {
        "label": "Houston, TX — humid, sprawling, no lake",
        "ring": geo.bbox_ring(-95.45, 29.70, -95.29, 29.83),
        "date": "2025-07-22",
    },
    "chicago": {
        "label": "Chicago, IL — baseline, lake-dominated",
        "ring": geo.bbox_ring(-87.75, 41.85, -87.60, 41.92),
        "date": "2025-07-22",
    },
}


def fetch(client: NightwatchClient, ring, date: str, hour: int) -> dict:
    r = client.heatmap(
        geo.feature_collection(ring), date, filter_type=1,
        start_time=f"{hour:02d}:00", granularity=GRAN, analytic_type="tcm",
    )
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        t = p.get("average_temperature")
        if t is None:
            continue
        g = (f.get("geometry", {}).get("coordinates") or [[]])[0]
        if not g:
            continue
        out[p.get("tile_id")] = (
            float(t),
            sum(c[0] for c in g) / len(g),
            sum(c[1] for c in g) / len(g),
        )
    return out


def hist(vals, bins=10, width=40) -> str:
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / span * bins))] += 1
    peak = max(counts) or 1
    return "\n".join(
        f"      {lo + span*i/bins:+7.3f} {'#'*int(width*n/peak):<{width}} {n}"
        for i, n in enumerate(counts))


def run(client: NightwatchClient, key: str) -> dict:
    cfg = CITIES[key]
    area = geo.polygon_area_sq_mi(cfg["ring"])
    print("\n" + "=" * 70)
    print(cfg["label"])
    print(f"{area:.1f} mi^2 at {GRAN} m, {cfg['date']}")
    print("=" * 70)

    day = fetch(client, cfg["ring"], cfg["date"], DAY_HOUR)
    night = fetch(client, cfg["ring"], cfg["date"], NIGHT_HOUR)
    ids = sorted(set(day) & set(night))
    print(f"  {len(ids)} tiles")
    if len(ids) < 200:
        print("  too few tiles; skipping")
        return {}

    d = [day[i][0] for i in ids]
    n = [night[i][0] for i in ids]
    lon = [day[i][1] for i in ids]
    lat = [day[i][2] for i in ids]
    cool = [a - b for a, b in zip(d, n)]

    a, b, c, r2 = ols2(lon, lat, n)
    resid = [n[i] - (a * lon[i] + b * lat[i] + c) for i in range(len(n))]

    res = {
        "city": key,
        "n": len(ids),
        "day_mean": statistics.mean(d), "day_sd": statistics.pstdev(d),
        "night_mean": statistics.mean(n), "night_sd": statistics.pstdev(n),
        "night_span": max(n) - min(n),
        "cool_mean": statistics.mean(cool),
        "rho": spearman(d, n),
        "night_plane_r2": r2,
        "retention_sd": statistics.pstdev(resid),
        "retention_span": max(resid) - min(resid),
    }

    print(f"  3pm    mean {res['day_mean']:6.2f}C  sd {res['day_sd']:5.3f}")
    print(f"  3am    mean {res['night_mean']:6.2f}C  sd {res['night_sd']:5.3f}  "
          f"span {res['night_span']:5.2f}C")
    print(f"  cooled mean {res['cool_mean']:6.2f}C")
    print(f"  day/night rho {res['rho']:+.3f}")
    print(f"\n  night temp explained by position (lon/lat plane): "
          f"R^2 = {r2:.3f}")
    print(f"  NIGHT HEAT RETENTION  sd {res['retention_sd']:.3f}C  "
          f"span {res['retention_span']:.2f}C")
    print("  distribution of retention (C above what position predicts):")
    print(hist(resid))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="phoenix",
                    choices=list(CITIES) + ["all"])
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient()

    keys = list(CITIES) if args.city == "all" else [args.city]
    out = [r for r in (run(client, k) for k in keys) if r]

    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"  {'city':<10} {'night sd':>9} {'plane R2':>9} "
          f"{'retention sd':>13} {'rho':>7}")
    for r in out:
        print(f"  {r['city']:<10} {r['night_sd']:9.3f} "
              f"{r['night_plane_r2']:9.3f} {r['retention_sd']:13.3f} "
              f"{r['rho']:+7.3f}")

    print("\n  Chicago's retention sd was 0.143C -- effectively flat.")
    best = max(out, key=lambda r: r["retention_sd"]) if out else None
    if best:
        s = best["retention_sd"]
        print(f"  Best here: {best['city']} at {s:.3f}C.")
        if s > 0.5:
            print("\n  => REAL BLOCK-LEVEL STRUCTURE. Chicago was the problem,")
            print("     not the model. Rebuild the map on this city and the")
            print("     thesis stands -- with Chicago kept as the honest")
            print("     counter-example where the lake dominates.")
        elif s < 0.25:
            print("\n  => STILL FLAT. The model does not resolve night-time")
            print("     urban structure anywhere, so no city will rescue the")
            print("     original thesis. Pivot to what the data DOES support:")
            print("     the daytime field has real spread (Chicago day sd was")
            print("     2.07C), and distance-to-water controls the night. That")
            print("     is a genuine, defensible finding about what drives")
            print("     nocturnal heat -- report it as the result rather than")
            print("     forcing a map that the data cannot carry.")
        else:
            print("\n  => MARGINAL. Better than Chicago but thin. Try 60 m")
            print("     granularity on a small AOI before committing --")
            print("     2.78x the tiles, ~2.78x the credits.")


if __name__ == "__main__":
    main()
