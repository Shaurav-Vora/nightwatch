#!/usr/bin/env python3
"""Step 2: does `time_of_measure` mean what we think it means?

The probe returned peak hours for a 0.6 mi^2 slice of the Chicago Loop with
median 2.0 UTC (= 21:00 local) and max 15.0 UTC (= 10:00 local). Two things
are wrong with that picture:

  * a 9pm median peak is extreme even for a strong thermal-lag story, and
  * the spread is bimodal inside one small, morphologically uniform AOI,
    which has no physical explanation.

Genuine lag, a mislabelled field, and a wrong timezone assumption all render
as an identical beautiful map. This script separates them, because it
compares places whose physics we already know:

    PARK      low thermal mass, evapotranspiration -> peaks near solar noon
    CORE      masonry and asphalt, stores energy   -> peaks hours later

If park tiles do NOT peak earlier than core tiles, we have a bug or a
misreading, not a discovery, and nothing downstream is trustworthy.

It also answers the second open question: does /satellite return a real
class mix, or only a dominant class? The Loop returned ['building'] alone.
A park is the control.

Costs 4 heatmap calls + 2 satellite calls. Run:  python validate_lag.py
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from nightwatch.timeutil import (  # noqa: E402
    CITY_TZ, convert_tiles, sanity_check_peak_hours, utc_hour_to_local,
)

PROBE_DATE = "2025-07-22"
TZ = CITY_TZ["chicago"]

# Two contrasting sites, ~5 km apart, same city, same day, same call shape.
SITES = {
    "PARK  (Jackson Park, lakefront green)": (-87.5806, 41.7844),
    "CORE  (the Loop, dense masonry)":       (-87.6298, 41.8781),
}


def peak_hours(client: NightwatchClient, lon: float, lat: float) -> list:
    aoi = geo.feature_collection(geo.square_aoi(lon, lat, 0.6))
    result = client.heatmap(
        aoi, PROBE_DATE, filter_type=3, granularity=100,
        analytic_type="time_of_measure",
    )
    feats = (result or {}).get("map_data", {}).get("features", [])
    converted = convert_tiles(feats, date.fromisoformat(PROBE_DATE), TZ)
    return [f["properties"]["local_hour"] for f in converted
            if "local_hour" in f["properties"]]


def temps_by_hour(client: NightwatchClient, lon: float, lat: float) -> dict:
    """Independent check: pull tcm at three hours and see where the max lands.

    If time_of_measure is telling the truth, the hour it names should be the
    hour at which tcm is highest. This is the cross-check that does not rely
    on trusting the analytic at all.
    """
    aoi = geo.feature_collection(geo.square_aoi(lon, lat, 0.6))
    out = {}
    for local_hour in (13, 17, 21):
        # convert desired LOCAL hour back to the UTC hour to request
        utc_hour = (local_hour + 5) % 24  # CDT = UTC-5
        r = client.heatmap(
            aoi, PROBE_DATE, filter_type=1, start_time=f"{utc_hour:02d}:00",
            granularity=100, analytic_type="tcm",
        )
        temps = [
            f["properties"].get("average_temperature")
            for f in (r or {}).get("map_data", {}).get("features", [])
            if f["properties"].get("average_temperature") is not None
        ]
        out[local_hour] = sum(temps) / len(temps) if temps else None
    return out


def histogram(hours: list, width: int = 40) -> str:
    counts = Counter(hours)
    if not counts:
        return "    (no data)"
    peak = max(counts.values())
    lines = []
    for h in range(24):
        n = counts.get(h, 0)
        if n:
            bar = "#" * max(1, int(width * n / peak))
            lines.append(f"    {h:02d}:00 {bar} {n}")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()  # cache ON -- reruns are free

    print("=" * 72)
    print("STEP 2: VALIDATING time_of_measure")
    print(f"date {PROBE_DATE}   tz {TZ}   (July = CDT = UTC-5)")
    print("=" * 72)

    results = {}
    for label, (lon, lat) in SITES.items():
        print(f"\n{label}")
        hours = peak_hours(client, lon, lat)
        results[label] = hours
        if not hours:
            print("    no tiles returned")
            continue
        mean = sum(hours) / len(hours)
        print(f"    n={len(hours)}  mean local peak {mean:.1f}h  "
              f"range {min(hours)}-{max(hours)}h")
        print(histogram(hours))

    keys = list(SITES)
    park, core = results.get(keys[0], []), results.get(keys[1], [])

    print("\n" + "-" * 72)
    print("SANITY CHECK: park must peak EARLIER than core")
    print("-" * 72)
    ok, msg = sanity_check_peak_hours(park, core)
    print("    " + msg.replace("\n", "\n    "))

    # ---- independent cross-check -------------------------------------
    print("\n" + "-" * 72)
    print("CROSS-CHECK: tcm at 13:00 / 17:00 / 21:00 local")
    print("if time_of_measure is right, the hottest of these three should")
    print("sit near the peak hour it reported for that site")
    print("-" * 72)
    for label, (lon, lat) in SITES.items():
        temps = temps_by_hour(client, lon, lat)
        shown = "  ".join(
            f"{h}:00={t:.1f}C" if t is not None else f"{h}:00=?"
            for h, t in temps.items()
        )
        valid = {h: t for h, t in temps.items() if t is not None}
        hottest = max(valid, key=valid.get) if valid else None
        print(f"  {label}")
        print(f"    {shown}    -> hottest at {hottest}:00 local")

    # ---- segmentation control ----------------------------------------
    print("\n" + "-" * 72)
    print("SEGMENTATION: does /satellite return a class mix, or one class?")
    print("-" * 72)
    for label, (lon, lat) in SITES.items():
        try:
            r = client.satellite(lat, lon, PROBE_DATE,
                                 start_time="14:00", granularity=80)
            segs = ((r or {}).get("segmentation") or {}).get("segments") or {}
            print(f"  {label}")
            print(f"    classes: {segs if segs else '(empty)'}")
        except Exception as e:
            print(f"  {label}\n    failed: {e}")

    print("\n" + "=" * 72)
    if ok:
        print("VERDICT: lag direction is physically correct. Proceed.")
    else:
        print("VERDICT: DO NOT PROCEED to harvesting.")
        print("Either the UTC assumption is wrong, or time_of_measure does")
        print("not mean peak hour. Compare the cross-check above: if tcm")
        print("peaks at 17:00 local but time_of_measure says 21:00, the")
        print("field is not what the docs claim and we reinterpret it.")
    print("=" * 72)


if __name__ == "__main__":
    main()
