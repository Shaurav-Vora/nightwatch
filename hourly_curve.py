#!/usr/bin/env python3
"""Step 3: build the diurnal curve ourselves, from tcm.

Why this exists
---------------
validate_lag.py showed `time_of_measure` contradicting `tcm`: plain
temperature says both sites peak at 13:00 and cool monotonically, while
time_of_measure claims 21:00 for the park and 16:00 for the core. The
values it returns are also tightly clustered (2.0 and 15.0 UTC in the Loop)
rather than smoothly distributed, which looks like satellite overpass times
-- i.e. *when the observation was taken*, not when the tile was hottest.

So we stop trusting it and compute peak hour from the primitive instead:
sample tcm at all 24 hours and take the argmax per tile. More calls, but
calls are currently costing zero credits, and a computed result we can show
our working for is worth more in a judged competition than a derived field
whose behaviour contradicts its documentation.

This script answers three questions at once
-------------------------------------------
1. WHICH CLOCK does `start_time` speak -- UTC or local?
   Self-calibrating: a diurnal curve's minimum sits just before sunrise
   (~05:00 local in Chicago in July) and its maximum mid-afternoon
   (~15:00). Wherever those land on the requested-hour axis reveals the
   offset. No assumption required.

2. IS THERE THERMAL LAG AT ALL?
   Per-tile argmax for park vs core. If the core peaks materially later,
   NightWatch's thesis is real and now rests on our own computation.

3. DOES NIGHT-TIME DIVERGENCE EXIST?
   The thesis is not really about peak timing -- it is about the core
   staying hotter after midnight. Reports the park-core gap hour by hour,
   which is the actual mortality-relevant quantity.

Cost: 24 hours x 2 sites = 48 heatmap calls, run concurrently, cached.
Roughly 3-4 minutes on first run, instant thereafter.

    python hourly_curve.py
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402

PROBE_DATE = "2025-07-22"
GRAN = 100
AOI_MI2 = 0.6

SITES = {
    "PARK": (-87.5806, 41.7844),   # Jackson Park / lakefront, 64% water
    "CORE": (-87.6298, 41.8781),   # the Loop, 100% building
}

# Chicago, 22 July: sunrise ~05:25 local, sunset ~20:20 local, CDT = UTC-5.
SUNRISE_LOCAL = 5
PEAK_EXPECTED_LOCAL = 15


def fetch_hour(client: NightwatchClient, lon: float, lat: float, hour: int):
    """One tcm call for a single hour. Returns {tile_id: temperature}."""
    aoi = geo.feature_collection(geo.square_aoi(lon, lat, AOI_MI2))
    r = client.heatmap(
        aoi, PROBE_DATE, filter_type=1, start_time=f"{hour:02d}:00",
        granularity=GRAN, analytic_type="tcm",
    )
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        t = p.get("average_temperature")
        if t is not None:
            out[p.get("tile_id")] = float(t)
    return out


def sweep(client: NightwatchClient, lon: float, lat: float,
          workers: int = 4) -> dict:
    """{hour: {tile_id: temp}} for all 24 hours, fetched concurrently.

    Partial results are returned rather than lost: one hour failing must not
    discard the other 23, all of which are already paid for and cached.
    Workers default to 4 rather than 8 -- the 504s appeared under 8-way
    concurrency, and the wall-clock difference is a couple of minutes.
    """
    results, failures = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_hour, client, lon, lat, h): h for h in range(24)
        }
        for fut, h in futures.items():
            try:
                results[h] = fut.result()
            except Exception as e:  # noqa: BLE001
                results[h] = {}
                failures[h] = f"{type(e).__name__}: {e}"

    if failures:
        print(f"  ! {len(failures)} of 24 hours failed; continuing with the rest")
        for h, msg in sorted(failures.items()):
            print(f"      {h:02d}:00  {msg[:110]}")
        print("      re-run to retry only these -- everything else is cached")
    return results


def mean_curve(series: dict) -> dict:
    return {
        h: (statistics.mean(t.values()) if t else None)
        for h, t in sorted(series.items())
    }


def per_tile_argmax(series: dict) -> list:
    """The hour at which each tile is hottest -- our own time_of_measure."""
    tiles = {}
    for hour, temps in series.items():
        for tid, t in temps.items():
            tiles.setdefault(tid, {})[hour] = t
    return [max(h_t, key=h_t.get) for h_t in tiles.values() if h_t]


def plot(curve: dict, width: int = 46) -> str:
    vals = [v for v in curve.values() if v is not None]
    if not vals:
        return "    (no data)"
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lines = []
    for h, v in curve.items():
        if v is None:
            lines.append(f"    {h:02d}  (none)")
            continue
        n = int(width * (v - lo) / span)
        lines.append(f"    {h:02d}  {'#' * n:<{width}} {v:5.1f}C")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()

    print("=" * 74)
    print("STEP 3: DIURNAL CURVE FROM tcm")
    print(f"date {PROBE_DATE}  granularity {GRAN}m  aoi {AOI_MI2} mi^2")
    print("48 calls, concurrent, cached -- first run takes a few minutes")
    print("=" * 74)

    data, curves, argmaxes = {}, {}, {}
    for name, (lon, lat) in SITES.items():
        print(f"\nsweeping {name} ...", flush=True)
        s = sweep(client, lon, lat)
        data[name] = s
        curves[name] = mean_curve(s)
        argmaxes[name] = per_tile_argmax(s)
        n_tiles = len(set().union(*[set(v) for v in s.values() if v]) or [])
        print(f"  {n_tiles} tiles x {sum(1 for v in s.values() if v)} hours")
        print(plot(curves[name]))

    # ---- Q1: which clock? -------------------------------------------
    print("\n" + "=" * 74)
    print("Q1  WHICH CLOCK DOES start_time SPEAK?")
    print("=" * 74)
    ref = curves["CORE"]
    valid = {h: v for h, v in ref.items() if v is not None}
    if valid:
        h_min = min(valid, key=valid.get)
        h_max = max(valid, key=valid.get)
        print(f"    coldest requested hour: {h_min:02d}:00   "
              f"hottest: {h_max:02d}:00")
        print(f"    expected if LOCAL: coldest ~{SUNRISE_LOCAL:02d}:00, "
              f"hottest ~{PEAK_EXPECTED_LOCAL:02d}:00")
        offset_min = (h_min - SUNRISE_LOCAL) % 24
        offset_max = (h_max - PEAK_EXPECTED_LOCAL) % 24
        print(f"    implied offset from the minimum: +{offset_min}h")
        print(f"    implied offset from the maximum: +{offset_max}h")
        if offset_min in (0, 1, 23) and offset_max in (0, 1, 23):
            print("    => start_time appears to be LOCAL time.")
        elif offset_min in (4, 5, 6) and offset_max in (4, 5, 6):
            print("    => start_time appears to be UTC (Chicago CDT = UTC-5).")
        else:
            print("    => inconclusive; inspect the curve shape by eye.")
            print("       A real diurnal curve is unmistakable: slow decline")
            print("       overnight, sharp minimum at sunrise, steep climb.")

    # ---- Q2: is there lag? ------------------------------------------
    print("\n" + "=" * 74)
    print("Q2  IS THERE THERMAL LAG? (per-tile argmax of tcm)")
    print("=" * 74)
    for name in SITES:
        a = argmaxes[name]
        if not a:
            continue
        c = Counter(a)
        print(f"  {name}: mean peak hour {statistics.mean(a):5.2f}   "
              f"mode {c.most_common(1)[0][0]:02d}:00   n={len(a)}")
        print(f"       distribution: "
              f"{dict(sorted(c.items()))}")
    if argmaxes["PARK"] and argmaxes["CORE"]:
        lag = statistics.mean(argmaxes["CORE"]) - statistics.mean(argmaxes["PARK"])
        print(f"\n  CORE minus PARK = {lag:+.2f} hours")
        if lag > 0.5:
            print("  => Lag exists and points the right way. Thesis holds,")
            print("     computed from tcm rather than taken on trust.")
        elif lag < -0.5:
            print("  => Lag is INVERTED. Park peaks later than the core.")
            print("     Note the park AOI is 64% water, which has enormous")
            print("     thermal mass -- a lake genuinely does peak late.")
            print("     Re-run against a DRY park before concluding anything.")
        else:
            print("  => No meaningful difference in peak timing.")
            print("     Peak timing may simply not discriminate at this")
            print("     scale. Look at Q3 -- night-time divergence is the")
            print("     mortality-relevant signal and may still be strong.")

    # ---- Q3: night divergence, the real thesis ----------------------
    print("\n" + "=" * 74)
    print("Q3  NIGHT-TIME DIVERGENCE (core minus park, per hour)")
    print("this, not peak timing, is what correlates with mortality")
    print("=" * 74)
    gaps = {}
    for h in range(24):
        a, b = curves["CORE"].get(h), curves["PARK"].get(h)
        if a is not None and b is not None:
            gaps[h] = a - b
    if gaps:
        widest = max(gaps, key=gaps.get)
        for h, g in gaps.items():
            bar = "+" * int(abs(g) * 8)
            flag = "  <-- widest" if h == widest else ""
            print(f"    {h:02d}  {g:+5.2f}C  {bar}{flag}")
        night = [g for h, g in gaps.items() if h >= 21 or h <= 4]
        day = [g for h, g in gaps.items() if 10 <= h <= 16]
        if night and day:
            print(f"\n    mean gap, day hours   : {statistics.mean(day):+.2f}C")
            print(f"    mean gap, night hours : {statistics.mean(night):+.2f}C")
            if statistics.mean(night) > statistics.mean(day):
                print("    => The core-park gap WIDENS at night. That is the")
                print("       NightWatch thesis, measured directly.")
            else:
                print("    => The gap does not widen at night at this site")
                print("       pair. Try a dry park and a denser core before")
                print("       drawing conclusions.")


if __name__ == "__main__":
    main()
