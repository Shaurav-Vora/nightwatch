#!/usr/bin/env python3
"""Task 1: lock the story. Degrees in, hours out.

Zero credits -- every call below is a cache hit on data already paid for.

The claim to test
-----------------
Across all 21 mi^2 of Phoenix we measured, the ENTIRE spread of afternoon
temperature is 1.30C. Look at that and the city appears uniform; nothing
demands action. Ask the same tiles a different question -- how many hours do
you spend above a danger threshold? -- and the spread is 5.46 hours.

If that holds across three cities, the finding is:

    Heat maps show temperature, where a whole city fits inside about a
    degree and nothing looks urgent. Expressed as hours of exposure, the
    same data spans hours. The variable is wrong, not the map.

What this measures, per city
----------------------------
  1. AMPLIFICATION -- degrees of temperature spread vs hours of exposure
     spread. The headline.
  2. SLOPE -- hours of extra exposure per degree, by regression. This is the
     sentence a city official understands: "one degree costs you N hours".
  3. R^2 and rho -- how much of persistence is predictable from peak
     temperature. Either answer supports the claim, differently:
       high R^2 -> duration is a magnifying lens on a signal too small to
                   read in degrees
       low  R^2 -> duration carries information temperature does not have
                   at all, which is stronger still
  4. TOP-20 OVERLAP -- would a temperature map send you to the same blocks?

Writes web/data/amplification.json for the frontend.

    python amplification.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from cooling_deficit import spearman  # noqa: E402
from city_test import CITIES  # noqa: E402
from duration_test import THRESHOLDS  # noqa: E402

GRAN = 100
DAY_HOUR = 15
OUT = Path("web/data/amplification.json")


def fetch_tcm(client, ring, date, hour):
    """Must match city_test.fetch's payload exactly so the cache hits."""
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


def fetch_persistence(client, ring, date, threshold):
    """Must match duration_test.fetch_analytic's payload exactly."""
    r = client.heatmap(
        geo.feature_collection(ring), date, filter_type=3, granularity=GRAN,
        analytic_type="persistence", threshold=threshold, direction="above",
    )
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        v = p.get("value")
        if v is None:
            continue
        # Chicago returned -0.65 h. Duration cannot be negative; the analytic
        # field is smoothed and overshoots below zero. Clamp and count them.
        out[p.get("tile_id")] = max(0.0, float(v))
    return out


def ols1(x, y):
    """slope, intercept, r2 for y = m*x + c."""
    mx, my = statistics.mean(x), statistics.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx == 0:
        return 0.0, my, 0.0
    m = sxy / sxx
    c = my - m * mx
    ss_tot = sum((b - my) ** 2 for b in y)
    ss_res = sum((b - (m * a + c)) ** 2 for a, b in zip(x, y))
    return m, c, (1 - ss_res / ss_tot if ss_tot else 0.0)


def scatter(x, y, cols=58, rows=16):
    lox, hix = min(x), max(x)
    loy, hiy = min(y), max(y)
    sx = (hix - lox) or 1.0
    sy = (hiy - loy) or 1.0
    grid = [[0] * cols for _ in range(rows)]
    for a, b in zip(x, y):
        c = min(cols - 1, int((a - lox) / sx * cols))
        r = min(rows - 1, int((hiy - b) / sy * rows))
        grid[r][c] += 1
    peak = max(max(r) for r in grid) or 1
    ramp = " .:-=+*#%@"
    lines = []
    for i, row in enumerate(grid):
        yv = hiy - sy * (i + 0.5) / rows
        bar = "".join(ramp[min(len(ramp) - 1, int(v / peak * (len(ramp) - 1)))]
                      if v else " " for v in row)
        lines.append(f"    {yv:5.1f}h |{bar}|")
    lines.append("          +" + "-" * cols + "+")
    lines.append(f"          {lox:.2f}C{' ' * (cols - 12)}{hix:.2f}C")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()
    results = {}

    print("=" * 74)
    print("TASK 1 — DEGREES IN, HOURS OUT")
    print("re-analysing cached data · zero credits")
    print("=" * 74)

    for key, cfg in CITIES.items():
        th = THRESHOLDS[key]
        print(f"\n{'=' * 74}\n{cfg['label']}\nthreshold {th}C\n{'=' * 74}")

        try:
            temp = fetch_tcm(client, cfg["ring"], cfg["date"], DAY_HOUR)
            pers = fetch_persistence(client, cfg["ring"], cfg["date"], th)
        except Exception as e:
            print(f"  could not load from cache: {type(e).__name__}: {e}")
            print("  (this city was never harvested, or the payload differs)")
            continue

        ids = sorted(set(temp) & set(pers))
        if len(ids) < 200:
            print(f"  only {len(ids)} matched tiles; skipping")
            continue

        t = [temp[i][0] for i in ids]
        h = [pers[i] for i in ids]

        t_span = max(t) - min(t)
        h_span = max(h) - min(h)
        slope, _, r2 = ols1(t, h)
        rho = spearman(t, h)

        k = 20
        hot_t = {i for i in sorted(ids, key=lambda i: -temp[i][0])[:k]}
        hot_h = {i for i in sorted(ids, key=lambda i: -pers[i])[:k]}

        zeros = sum(1 for v in h if v == 0.0)

        print(f"  {len(ids)} tiles")
        print(f"\n  TEMPERATURE  span {t_span:5.2f}C   sd {statistics.pstdev(t):5.3f}C")
        print(f"  EXPOSURE     span {h_span:5.2f}h   sd {statistics.pstdev(h):5.3f}h")
        print(f"\n  AMPLIFICATION  {h_span:.2f} h of exposure across "
              f"{t_span:.2f} C of temperature")
        print(f"  SLOPE          {slope:+.2f} hours of exposure per degree C")
        print(f"  R^2            {r2:.3f}   (how much of exposure peak temp explains)")
        print(f"  rho            {rho:+.3f}")
        print(f"  top-{k} overlap  {len(hot_t & hot_h)}/{k} "
              f"(hottest blocks vs longest-exposed blocks)")
        if zeros:
            print(f"  {zeros} tiles clamped from negative to zero hours")

        print(f"\n  exposure (hours) vs afternoon temperature (C):")
        print(scatter(t, h))

        results[key] = {
            "label": cfg["label"], "threshold_c": th, "n": len(ids),
            "temp_span_c": round(t_span, 3),
            "temp_sd_c": round(statistics.pstdev(t), 3),
            "exposure_span_h": round(h_span, 3),
            "exposure_sd_h": round(statistics.pstdev(h), 3),
            "exposure_mean_h": round(statistics.mean(h), 2),
            "slope_h_per_c": round(slope, 3),
            "r2": round(r2, 3),
            "rho": round(rho, 3),
            "top20_overlap": len(hot_t & hot_h),
            "clamped_zero": zeros,
        }

    # ---------------------------------------------------------------
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    print(f"  {'city':<9} {'temp span':>10} {'exposure span':>14} "
          f"{'h per C':>8} {'R2':>6} {'top20':>6}")
    for k, r in results.items():
        print(f"  {k:<9} {r['temp_span_c']:9.2f}C {r['exposure_span_h']:13.2f}h "
              f"{r['slope_h_per_c']:8.2f} {r['r2']:6.3f} "
              f"{r['top20_overlap']:5d}/20")

    if results:
        worst = min(results.values(), key=lambda r: r["temp_span_c"])
        print(f"\n  Tightest temperature range: {worst['temp_span_c']:.2f}C "
              f"-> {worst['exposure_span_h']:.2f}h of exposure.")
        print(f"  A city that looks uniform in degrees is not uniform in hours.")

        r2s = [r["r2"] for r in results.values()]
        if statistics.mean(r2s) > 0.7:
            print("\n  Exposure is largely predictable from peak temperature,")
            print("  so duration is acting as a MAGNIFYING LENS: it converts a")
            print("  signal too small to read in degrees into one measured in")
            print("  hours. That is the argument -- say it exactly that way,")
            print("  and do not imply duration is independent information.")
        elif statistics.mean(r2s) < 0.4:
            print("\n  Exposure is NOT well predicted by peak temperature, so")
            print("  duration carries information temperature does not have.")
            print("  Stronger claim than magnification -- lead with it.")
        else:
            print("\n  Partly predictable. Report R^2 per city and let the")
            print("  amplification figure carry the argument.")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(results, indent=2))
        print(f"\n  wrote {OUT}")


if __name__ == "__main__":
    main()
