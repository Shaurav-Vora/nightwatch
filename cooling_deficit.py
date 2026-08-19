#!/usr/bin/env python3
"""Step 4: does the day map disagree with the night map, across a whole city?

What changed and why
--------------------
Step 3 compared two sites and found the right shape but weak instruments:

  * peak hour is discrete and noisy -- CORE's tiles split {15: 48, 17: 60}
    with nothing at 16, because 15:00 and 17:00 tied at 28.0C exactly. An
    argmax over a flat top is close to a coin flip.
  * the night gap was real but small (+0.30C mean).
  * but NOCTURNAL COOLING was large and clean: the park shed 9.6C between
    its afternoon peak and its pre-dawn minimum, the core only 6.5C. A 3.1C
    difference in overnight recovery.

So the hero variable becomes cooling deficit, not peak hour:

    CoolingDeficit(tile) = T(15:00 local) - T(03:00 local)

Low deficit = this block never lets go of its heat = nobody living here got
a recovery night. It is continuous, robust to ties, physically meaningful,
and costs two calls per AOI instead of twenty-four.

Two sites cannot tell you whether that works as a *map*. This script asks
the question that matters for the whole project:

    Q1  Does cooling deficit vary meaningfully across thousands of tiles,
        or is it roughly constant (in which case there is no map)?

    Q2  DOES THE NIGHT RANKING DISAGREE WITH THE DAY RANKING?
        Spearman rank correlation between T(15:00) and T(03:00) across all
        tiles. If it is near 1.0, the daytime map already tells you
        everything and NightWatch has no reason to exist. If it is low, the
        two maps genuinely rank neighbourhoods differently -- and that
        number is the headline statistic for the submission.

    Q3  Of the 20 hottest tiles by day, how many are in the 20 worst by
        night? The same finding in a form a judge grasps in two seconds.

Cost: 2 heatmap calls over one ~37 mi^2 AOI. Run:  python cooling_deficit.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402

PROBE_DATE = "2025-07-22"
GRAN = 100
DAY_HOUR, NIGHT_HOUR = 15, 3   # local, per Step 3's calibration

# A cross-section of Chicago: lakefront -> Loop -> near west side -> Garfield
# Park. Deliberately spans the full morphological range in one AOI.
AOI_RING = geo.bbox_ring(west=-87.75, south=41.85, east=-87.60, north=41.92)


def fetch(client: NightwatchClient, hour: int) -> dict:
    """{tile_id: (temp, lon, lat)} for one local hour."""
    r = client.heatmap(
        geo.feature_collection(AOI_RING), PROBE_DATE,
        filter_type=1, start_time=f"{hour:02d}:00",
        granularity=GRAN, analytic_type="tcm",
    )
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        t = p.get("average_temperature")
        if t is None:
            continue
        ring = (f.get("geometry", {}).get("coordinates") or [[]])[0]
        if ring:
            lon = sum(c[0] for c in ring) / len(ring)
            lat = sum(c[1] for c in ring) / len(ring)
        else:
            lon = lat = None
        out[p.get("tile_id")] = (float(t), lon, lat)
    return out


def rank(values: list) -> list:
    """Ranks, averaging ties -- required for a correct Spearman."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list, b: list) -> float:
    ra, rb = rank(a), rank(b)
    n = len(ra)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else float("nan")


def histogram(vals: list, bins: int = 12, width: int = 44) -> str:
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / span * bins))] += 1
    peak = max(counts) or 1
    lines = []
    for i, n in enumerate(counts):
        edge = lo + span * i / bins
        lines.append(f"    {edge:5.2f}C {'#' * int(width * n / peak):<{width}} {n}")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()

    area = geo.polygon_area_sq_mi(AOI_RING)
    print("=" * 74)
    print("STEP 4: COOLING DEFICIT ACROSS A CITY CROSS-SECTION")
    print(f"date {PROBE_DATE}   aoi {area:.1f} mi^2   granularity {GRAN}m")
    print(f"two calls: {DAY_HOUR:02d}:00 and {NIGHT_HOUR:02d}:00 local")
    print("=" * 74)

    print(f"\nfetching {DAY_HOUR:02d}:00 ...", flush=True)
    day = fetch(client, DAY_HOUR)
    print(f"  {len(day)} tiles")

    print(f"fetching {NIGHT_HOUR:02d}:00 ...", flush=True)
    night = fetch(client, NIGHT_HOUR)
    print(f"  {len(night)} tiles")

    shared = sorted(set(day) & set(night))
    print(f"\ntiles present in both: {len(shared)}")
    if len(shared) < 50:
        print("!! too few shared tiles -- tile_id may not be stable across")
        print("   calls. If so, join on geometry centroid instead.")
        return

    d = [day[t][0] for t in shared]
    n = [night[t][0] for t in shared]
    deficit = [dd - nn for dd, nn in zip(d, n)]

    # ---- Q1 -----------------------------------------------------------
    print("\n" + "=" * 74)
    print("Q1  DOES COOLING DEFICIT VARY? (if it is flat, there is no map)")
    print("=" * 74)
    print(f"    day   {DAY_HOUR:02d}:00  mean {statistics.mean(d):5.2f}C  "
          f"range {min(d):5.2f} - {max(d):5.2f}  sd {statistics.pstdev(d):4.2f}")
    print(f"    night {NIGHT_HOUR:02d}:00  mean {statistics.mean(n):5.2f}C  "
          f"range {min(n):5.2f} - {max(n):5.2f}  sd {statistics.pstdev(n):4.2f}")
    print(f"\n    COOLING  mean {statistics.mean(deficit):5.2f}C  "
          f"range {min(deficit):5.2f} - {max(deficit):5.2f}  "
          f"sd {statistics.pstdev(deficit):4.2f}")
    print(f"    spread across the city: {max(deficit) - min(deficit):.2f}C")
    print("\n    distribution of overnight cooling:")
    print(histogram(deficit))
    if max(deficit) - min(deficit) < 1.0:
        print("\n    => Nearly flat. Cooling deficit does not discriminate")
        print("       at this scale. Reconsider the hero variable.")
    else:
        print(f"\n    => {max(deficit) - min(deficit):.2f}C of spread. The worst tile keeps")
        print(f"       {max(deficit) - min(deficit):.2f}C more of its heat than the best.")

    # ---- Q2, the headline ---------------------------------------------
    print("\n" + "=" * 74)
    print("Q2  DOES THE NIGHT MAP DISAGREE WITH THE DAY MAP?")
    print("=" * 74)
    rho = spearman(d, n)
    print(f"    Spearman rho between day and night temperature = {rho:+.3f}")
    print(f"    rank agreement: {rho * 100:.0f}%")
    if rho > 0.85:
        print("\n    => The maps largely AGREE. A daytime map would tell you")
        print("       most of what the night map does. This is the result")
        print("       that would weaken the project -- report it honestly")
        print("       and pivot to cooling deficit as the story instead of")
        print("       'the rankings differ'.")
    elif rho < 0.5:
        print("\n    => The maps substantially DISAGREE. This is the headline.")
        print("       Knowing the hottest afternoon blocks tells you little")
        print("       about which blocks stay dangerous overnight.")
    else:
        print("\n    => Partial agreement. Real but not total divergence;")
        print("       state the number plainly rather than overselling it.")

    # ---- Q3 ------------------------------------------------------------
    print("\n" + "=" * 74)
    print("Q3  TOP-20 OVERLAP (the two-second version)")
    print("=" * 74)
    k = min(20, len(shared) // 4)
    hottest_day = {t for t in sorted(shared, key=lambda t: -day[t][0])[:k]}
    hottest_night = {t for t in sorted(shared, key=lambda t: -night[t][0])[:k]}
    worst_cooling = {t for t in sorted(
        shared, key=lambda t: day[t][0] - night[t][0])[:k]}
    print(f"    of the {k} hottest tiles at {DAY_HOUR:02d}:00, "
          f"{len(hottest_day & hottest_night)} are also in the "
          f"{k} hottest at {NIGHT_HOUR:02d}:00")
    print(f"    of the {k} hottest tiles at {DAY_HOUR:02d}:00, "
          f"{len(hottest_day & worst_cooling)} are also in the "
          f"{k} worst-cooling")
    print(f"\n    => a daytime heat map would send you to the wrong "
          f"{k - len(hottest_day & worst_cooling)} of {k} blocks")

    # ---- worst blocks --------------------------------------------------
    print("\n" + "-" * 74)
    print("WORST 10 TILES BY COOLING DEFICIT (the refuge-desert candidates)")
    print("-" * 74)
    print(f"    {'lat':>9} {'lon':>10} {'day':>7} {'night':>7} {'cooled':>7}")
    for t in sorted(shared, key=lambda t: day[t][0] - night[t][0])[:10]:
        dt, lon, lat = day[t]
        nt = night[t][0]
        print(f"    {lat:9.5f} {lon:10.5f} {dt:6.2f}C {nt:6.2f}C "
              f"{dt - nt:6.2f}C")
    print("\n    paste one of these lat/lon into Google Maps -- if the worst")
    print("    coolers are visibly dense/paved, the physics is holding.")


if __name__ == "__main__":
    main()
