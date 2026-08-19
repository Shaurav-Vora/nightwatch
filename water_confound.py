#!/usr/bin/env python3
"""Step 5: is rho = -0.731 a real urban finding, or just Lake Michigan?

The concern
-----------
Step 4 reported a striking result: Spearman rho of -0.731 between the 15:00
map and the 03:00 map, and zero overlap between the twenty hottest tiles by
day and the twenty hottest by night.

But the ten worst-cooling tiles all sat within ~300 m of each other around
(41.897, -87.617), were the coldest tiles by day AND the warmest at night,
and shed only 1.7C overnight. That is the thermal signature of water, not of
a neighbourhood. The AOI's eastern edge is -87.60 and the shoreline sits near
-87.61, so Lake Michigan is inside the box.

The cooling histogram was also bimodal -- ~1,400 tiles at 1.7-4.2C and ~6,000
at 9.9-11.6C with a near-empty middle. Two populations, not one gradient.

If the correlation is driven by land-versus-water, the finding says nothing
about urban form and any Chicago-literate judge will dismantle it instantly.

This script costs ZERO heatmap credits -- both hours are already cached. It
re-partitions the same tiles and recomputes. Optionally spends 2-3 satellite
calls to confirm what the suspect tiles actually are.

    python water_confound.py              # free, cache only
    python water_confound.py --verify     # + 3 satellite calls
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
from cooling_deficit import (  # noqa: E402
    AOI_RING, DAY_HOUR, GRAN, NIGHT_HOUR, PROBE_DATE, fetch, spearman,
)

# Safely inland: west of this longitude there is no Lake Michigan at these
# latitudes. Chosen with margin rather than hugging the shoreline.
INLAND_LON = -87.64
# A tile shedding less than this overnight is behaving like water.
WATER_COOLING_MAX = 5.0


def report(label: str, day: list, night: list) -> float:
    if len(day) < 30:
        print(f"  {label:<28} too few tiles ({len(day)})")
        return float("nan")
    rho = spearman(day, night)
    cooling = [d - n for d, n in zip(day, night)]
    print(f"  {label:<28} n={len(day):5d}  rho={rho:+.3f}  "
          f"cooling {statistics.mean(cooling):5.2f}C "
          f"(sd {statistics.pstdev(cooling):4.2f})")
    return rho


def ascii_map(tiles: dict, day: dict, night: dict,
              cols: int = 62, rows: int = 22) -> str:
    """Coarse spatial view of cooling deficit. Reveals whether the low
    coolers form a coherent shape (a lake, a river) or are scattered."""
    lons = [v[1] for v in tiles.values() if v[1] is not None]
    lats = [v[2] for v in tiles.values() if v[2] is not None]
    if not lons:
        return "(no geometry)"
    w, e, s, n = min(lons), max(lons), min(lats), max(lats)

    grid = [[[] for _ in range(cols)] for _ in range(rows)]
    for tid, (_, lon, lat) in tiles.items():
        if lon is None or tid not in day or tid not in night:
            continue
        c = min(cols - 1, int((lon - w) / ((e - w) or 1) * cols))
        r = min(rows - 1, int((n - lat) / ((n - s) or 1) * rows))
        grid[r][c].append(day[tid][0] - night[tid][0])

    ramp = " .:-=+*#%@"   # low cooling -> high cooling
    out = []
    for row in grid:
        line = ""
        for cell in row:
            if not cell:
                line += " "
            else:
                m = statistics.mean(cell)
                idx = int(max(0.0, min(1.0, (m - 1.5) / 10.0)) * (len(ramp) - 1))
                line += ramp[idx]
        out.append("    " + line)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="spend 3 satellite calls to confirm tile land cover")
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient()

    print("=" * 74)
    print("STEP 5: IS THE CORRELATION JUST THE LAKE?")
    print("re-analysing cached data -- zero heatmap credits")
    print("=" * 74)

    day = fetch(client, DAY_HOUR)      # cache hits
    night = fetch(client, NIGHT_HOUR)
    shared = sorted(set(day) & set(night))
    print(f"\n{len(shared)} tiles loaded from cache")

    print("\n" + "-" * 74)
    print("SPATIAL VIEW OF OVERNIGHT COOLING")
    print("space/dot = keeps its heat    @ = cools strongly")
    print("if the low-cooling zone is a coherent blob on one edge, it is water")
    print("-" * 74)
    print(ascii_map(day, day, night))

    # ---------------------------------------------------------------
    print("\n" + "=" * 74)
    print("RECOMPUTING ON SUBSETS")
    print("=" * 74)

    d_all = [day[t][0] for t in shared]
    n_all = [night[t][0] for t in shared]
    rho_all = report("ALL TILES (step 4)", d_all, n_all)

    inland = [t for t in shared if day[t][1] is not None
              and day[t][1] < INLAND_LON]
    rho_inland = report(f"INLAND ONLY (lon<{INLAND_LON})",
                        [day[t][0] for t in inland],
                        [night[t][0] for t in inland])

    landlike = [t for t in shared
                if (day[t][0] - night[t][0]) > WATER_COOLING_MAX]
    rho_land = report(f"COOLING>{WATER_COOLING_MAX}C (land-like)",
                      [day[t][0] for t in landlike],
                      [night[t][0] for t in landlike])

    waterlike = [t for t in shared
                 if (day[t][0] - night[t][0]) <= WATER_COOLING_MAX]
    print(f"\n  suspected water tiles: {len(waterlike)} "
          f"({100 * len(waterlike) / len(shared):.1f}% of the AOI)")
    if waterlike:
        wl = [day[t][1] for t in waterlike if day[t][1] is not None]
        wa = [day[t][2] for t in waterlike if day[t][2] is not None]
        if wl:
            print(f"  their bounding box: lon {min(wl):.4f} to {max(wl):.4f}, "
                  f"lat {min(wa):.4f} to {max(wa):.4f}")
            print(f"  east of {INLAND_LON}? "
                  f"{100 * sum(1 for x in wl if x >= INLAND_LON) / len(wl):.0f}% "
                  f"of them")

    # ---------------------------------------------------------------
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    if rho_land == rho_land and rho_land < -0.4:
        print("  Land-only rho is still strongly negative.")
        print("  => The reversal is NOT just the lake. The finding survives,")
        print("     and you should report BOTH numbers: the headline and the")
        print("     water-excluded control. Pre-empting the obvious")
        print("     objection is worth more than the bigger number.")
    elif rho_land == rho_land and rho_land > -0.15:
        print("  Land-only rho collapses toward zero.")
        print("  => Step 4's headline WAS the lake. Do not use -0.731.")
        print("     Water has huge thermal mass; that it is cool by day and")
        print("     warm at night is textbook, not a discovery.")
        print("     Pivot: mask water, and let cooling deficit across LAND")
        print("     tiles be the variable. It may still discriminate well --")
        print("     check the land-only cooling spread above.")
    else:
        print("  Land-only rho is weakened but not gone. Report it honestly,")
        print("  lead with the water-excluded number, and show the raw one")
        print("  alongside so nobody can accuse you of hiding it.")

    print("\n  RULE FOR THE REST OF THE PROJECT: mask water before computing")
    print("  any statistic. It is a different physical regime and it will")
    print("  dominate every correlation you run.")

    # ---------------------------------------------------------------
    if args.verify:
        print("\n" + "=" * 74)
        print("GROUND TRUTH: what are these tiles actually made of?")
        print("=" * 74)
        worst = sorted(shared, key=lambda t: day[t][0] - night[t][0])[0]
        best = sorted(shared, key=lambda t: -(day[t][0] - night[t][0]))[0]
        mid = sorted(shared, key=lambda t: abs(
            (day[t][0] - night[t][0]) - 9.0))[0]
        for label, tid in (("worst cooler", worst), ("median cooler", mid),
                           ("best cooler", best)):
            _, lon, lat = day[tid]
            try:
                r = client.satellite(lat, lon, PROBE_DATE,
                                     start_time="14:00", granularity=80)
                segs = ((r or {}).get("segmentation") or {}).get("segments") or {}
                print(f"  {label:<15} ({lat:.4f}, {lon:.4f})  "
                      f"cooled {day[tid][0] - night[tid][0]:.2f}C")
                print(f"                  {segs}")
            except Exception as e:
                print(f"  {label:<15} failed: {e}")


if __name__ == "__main__":
    main()
