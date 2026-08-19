#!/usr/bin/env python3
"""Step 6: after removing the lake gradient, is there any map left?

The problem
-----------
Step 5's ground truth landed three tiles in perfect longitude order:

    82% vegetation      cools  1.73C   lon -87.6186  (near lake)
    99% building        cools  9.00C   lon -87.6486  (middle)
    43% bldg/56% earth  cools 11.62C   lon -87.7498  (far inland)

So the apparent land-cover effect is confounded with distance from Lake
Michigan. The ASCII map showed the same thing: a smooth monotonic west-east
ramp, not block-level texture.

And inland-only cooling was 10.57C with sd 0.70 -- nearly constant. The
night field overall has sd 0.67C across 9,606 tiles.

NightWatch is a map. A map needs spatial variation that is not a single
smooth gradient. So:

    Q1  How much of cooling deficit is explained by position alone
        (a plane fitted to lon/lat)? High R^2 = it is one gradient.

    Q2  What is the RESIDUAL after removing that plane? If residual sd is
        tiny, there is no block-level signal and NightWatch as conceived
        does not work on this data.

    Q3  Is the residual STRUCTURED or NOISE? Structured residuals mean real
        urban features the gradient does not explain -- that is the map.
        Noise means the model is smooth and we are mapping interpolation.
        Tested by comparing neighbouring-tile differences against the
        overall spread: if neighbours differ as much as random pairs, it
        is noise.

Costs zero credits -- pure re-analysis of cached tiles.

    python detrend.py
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch.client import NightwatchClient  # noqa: E402
from cooling_deficit import DAY_HOUR, NIGHT_HOUR, fetch  # noqa: E402

WATER_COOLING_MAX = 5.0


def ols2(x1: list, x2: list, y: list) -> tuple:
    """Fit y = a*x1 + b*x2 + c by least squares. Returns (a, b, c, r2)."""
    n = len(y)
    m1, m2, my = statistics.mean(x1), statistics.mean(x2), statistics.mean(y)
    c11 = sum((a - m1) ** 2 for a in x1)
    c22 = sum((b - m2) ** 2 for b in x2)
    c12 = sum((a - m1) * (b - m2) for a, b in zip(x1, x2))
    c1y = sum((a - m1) * (t - my) for a, t in zip(x1, y))
    c2y = sum((b - m2) * (t - my) for b, t in zip(x2, y))

    det = c11 * c22 - c12 * c12
    if abs(det) < 1e-12:
        return 0.0, 0.0, my, 0.0
    a = (c1y * c22 - c2y * c12) / det
    b = (c2y * c11 - c1y * c12) / det
    c = my - a * m1 - b * m2

    ss_tot = sum((t - my) ** 2 for t in y)
    ss_res = sum((t - (a * u + b * v + c)) ** 2 for u, v, t in zip(x1, x2, y))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return a, b, c, r2


def ascii_field(pts: list, cols: int = 62, rows: int = 22,
                lo: float = None, hi: float = None) -> str:
    """pts = [(lon, lat, value)]"""
    if not pts:
        return "(empty)"
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    vals = [p[2] for p in pts]
    w, e, s, n = min(lons), max(lons), min(lats), max(lats)
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    span = (hi - lo) or 1.0

    grid = [[[] for _ in range(cols)] for _ in range(rows)]
    for lon, lat, v in pts:
        c = min(cols - 1, int((lon - w) / ((e - w) or 1) * cols))
        r = min(rows - 1, int((n - lat) / ((n - s) or 1) * rows))
        grid[r][c].append(v)

    ramp = " .:-=+*#%@"
    out = []
    for row in grid:
        line = ""
        for cell in row:
            if not cell:
                line += " "
            else:
                v = statistics.mean(cell)
                idx = int(max(0.0, min(1.0, (v - lo) / span)) * (len(ramp) - 1))
                line += ramp[idx]
        out.append("    " + line)
    return "\n".join(out)


def neighbour_roughness(tiles: list, resid: dict) -> tuple:
    """Compare adjacent-tile residual differences against random pairs.

    Smooth interpolated fields have neighbours that are far more similar
    than random pairs. Real block-level structure sits somewhere between
    'identical neighbours' and 'pure noise'.
    """
    by_pos = {}
    for tid, lon, lat in tiles:
        by_pos[(round(lon, 4), round(lat, 4))] = resid[tid]
    keys = list(by_pos)
    if len(keys) < 100:
        return float("nan"), float("nan")

    # grid step, from the smallest positive longitude gap
    lons = sorted({k[0] for k in keys})
    steps = [b - a for a, b in zip(lons, lons[1:]) if b - a > 1e-6]
    step = min(steps) if steps else 0.001

    diffs = []
    for (lon, lat), v in by_pos.items():
        right = by_pos.get((round(lon + step, 4), round(lat, 4)))
        if right is not None:
            diffs.append(abs(v - right))
    if not diffs:
        return float("nan"), float("nan")

    vals = list(by_pos.values())
    import random
    random.seed(0)
    rand = [abs(random.choice(vals) - random.choice(vals))
            for _ in range(min(5000, len(vals) * 2))]
    return statistics.mean(diffs), statistics.mean(rand)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()

    print("=" * 74)
    print("STEP 6: IS THERE A MAP AFTER THE GRADIENT IS REMOVED?")
    print("zero credits -- cached re-analysis")
    print("=" * 74)

    day = fetch(client, DAY_HOUR)
    night = fetch(client, NIGHT_HOUR)
    shared = [t for t in sorted(set(day) & set(night))
              if day[t][1] is not None]

    land = [t for t in shared
            if (day[t][0] - night[t][0]) > WATER_COOLING_MAX]
    print(f"\n{len(shared)} tiles, {len(land)} after excluding water-like")

    lon = [day[t][1] for t in land]
    lat = [day[t][2] for t in land]
    cooling = [day[t][0] - night[t][0] for t in land]
    nightT = [night[t][0] for t in land]

    # ---- Q1 -----------------------------------------------------------
    print("\n" + "=" * 74)
    print("Q1  HOW MUCH IS JUST POSITION?")
    print("=" * 74)
    for label, y in (("cooling deficit", cooling), ("night temperature", nightT)):
        a, b, c, r2 = ols2(lon, lat, y)
        print(f"  {label:<20} R^2 vs (lon,lat) plane = {r2:.3f}   "
              f"sd {statistics.pstdev(y):.3f}C")
        if r2 > 0.8:
            print(f"       => {r2*100:.0f}% is a smooth gradient, not urban structure")

    # ---- Q2 -----------------------------------------------------------
    print("\n" + "=" * 74)
    print("Q2  WHAT SURVIVES THE DETREND?")
    print("=" * 74)
    a, b, c, r2 = ols2(lon, lat, cooling)
    resid = {t: cooling[i] - (a * lon[i] + b * lat[i] + c)
             for i, t in enumerate(land)}
    rv = list(resid.values())
    sd_raw = statistics.pstdev(cooling)
    sd_res = statistics.pstdev(rv)
    print(f"  raw cooling sd      {sd_raw:6.3f}C")
    print(f"  residual sd         {sd_res:6.3f}C  "
          f"({100*sd_res/sd_raw:.0f}% of the original spread)")
    print(f"  residual range      {min(rv):+.3f} to {max(rv):+.3f}C")

    if sd_res < 0.25:
        print("\n  => Almost nothing survives. The field is one smooth")
        print("     gradient plus noise. There is no block-level map here.")
    elif sd_res < 0.6:
        print("\n  => Weak residual structure. A map is possible but the")
        print("     variation is small; be careful not to oversell colour")
        print("     differences that amount to a few tenths of a degree.")
    else:
        print("\n  => Substantial residual variation. Real structure beyond")
        print("     the gradient. This is the map.")

    print("\n  RESIDUAL FIELD (dark = cools less than position predicts)")
    print("  coherent blobs = real features. salt-and-pepper = noise.")
    print(ascii_field([(lon[i], lat[i], resid[t])
                       for i, t in enumerate(land)]))

    # ---- Q3 -----------------------------------------------------------
    print("\n" + "=" * 74)
    print("Q3  IS THE RESIDUAL STRUCTURE, OR NOISE?")
    print("=" * 74)
    nb, rnd = neighbour_roughness(
        [(t, day[t][1], day[t][2]) for t in land], resid)
    if nb == nb:
        print(f"  mean |difference| between ADJACENT tiles : {nb:.4f}C")
        print(f"  mean |difference| between RANDOM  tiles : {rnd:.4f}C")
        ratio = nb / rnd if rnd else float('nan')
        print(f"  ratio {ratio:.3f}")
        if ratio < 0.15:
            print("\n  => Neighbours are far more alike than random pairs.")
            print("     Strongly spatially autocorrelated -- but that is also")
            print("     exactly what a smoothly interpolated model looks like.")
            print("     Check the residual map above: coherent blobs are real,")
            print("     a single soft ramp means we are mapping interpolation.")
        elif ratio > 0.7:
            print("\n  => Neighbours differ almost as much as random pairs.")
            print("     The residual is essentially noise. Do not map it.")
        else:
            print("\n  => Genuine local structure: neighbours correlated but")
            print("     not identical. This is what a real map looks like.")

    # ---- candidates for paid segmentation ------------------------------
    print("\n" + "=" * 74)
    print("HIGHEST-VALUE TILES TO SPEND SEGMENTATION CREDITS ON")
    print("(matched pairs: same longitude band, opposite residual)")
    print("=" * 74)
    band = [t for t in land if -87.70 < day[t][1] < -87.66]
    if len(band) > 20:
        band.sort(key=lambda t: resid[t])
        print(f"  {len(band)} tiles in the lon band -87.70..-87.66")
        print(f"  {'':4}{'lat':>9} {'lon':>10} {'cooled':>8} {'resid':>8}")
        for tag, tiles in (("KEEPS HEAT", band[:3]),
                           ("SHEDS HEAT", band[-3:])):
            print(f"  {tag}")
            for t in tiles:
                print(f"      {day[t][2]:9.5f} {day[t][1]:10.5f} "
                      f"{day[t][0]-night[t][0]:7.2f}C {resid[t]:+7.3f}C")
        print("\n  These are longitude-matched, so segmenting a few of each")
        print("  tests land cover WITHOUT the lake-distance confound.")
        print(f"  6 satellite calls = ~86,400 credits. Only spend it if the")
        print(f"  residual above looks structured.")


if __name__ == "__main__":
    main()
