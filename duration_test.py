#!/usr/bin/env python3
"""Step 9: does DURATION discriminate where temperature does not?

The case for this test
----------------------
Eight steps of measuring temperature produced a consistent picture:

    city      day sd    night sd   retention sd   day/night rho
    phoenix    0.365      0.320        0.303          +0.234
    houston    1.023      0.338        0.283          +0.842
    chicago    2.071      0.672        0.402          -0.731

Night temperature is flat everywhere (~0.3C). The Chicago "reversal" was
Lake Michigan -- both lake-free cities show POSITIVE correlation. And
Phoenix, which has one of the best-documented urban heat islands on earth,
shows 0.365C of daytime spread across 83 mi^2, which cannot be right as
physics and must be a property of the field.

FortyGuard's own published measurements say exactly this:

    "daily-peak spread collapses as the AOI shrinks (5.85 C citywide ->
     0.94 C at 14 km^2 -> 0.90 C at 1.2 km^2) while exceedance spread stays
     large (6.5 h and 15.2 h). Peak temperature stops discriminating below
     city scale; duration keeps discriminating."

We have our own evidence too. The day-one probe ran persistence on a single
0.6 mi^2 AOI and got min 1.71 h, max 11.10 h, median 6.19 h -- 9.4 hours of
spread where temperature gave fractions of a degree.

So: temperature was the wrong instrument. Duration above a threshold is both
the discriminating variable AND the physiologically correct one, because it
is sustained exposure that denies the body its recovery window, not the peak.

    persistence = longest UNBROKEN run of hours above threshold
    exceedance  = TOTAL hours above threshold (may be scattered)

The distinction matters: 8 consecutive dangerous hours is a heat-stroke
event, 8 scattered ones is an inconvenience. Persistence is the
mortality-relevant one and almost nobody will use it.

Cost: 2 calls per city (~4,200 credits each at these AOI sizes).

    python duration_test.py
    python duration_test.py --city phoenix
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

GRAN = 100

# Thresholds sit midway between each city's measured 3pm and 3am means, so
# the day partly clears them and partly does not -- that is where duration
# has room to vary. A threshold nothing crosses, or everything crosses for
# 24 h, measures nothing.
THRESHOLDS = {
    "phoenix": 35.0,   # measured 38.33 day / 31.31 night
    "houston": 32.0,   # 36.20 / 27.29
    "chicago": 25.0,   # 29.99 / 20.74
}

# What temperature achieved, for the side-by-side.
TEMP_SD = {"phoenix": 0.320, "houston": 0.338, "chicago": 0.672}


def fetch_analytic(client, ring, date, analytic, threshold):
    r = client.heatmap(
        geo.feature_collection(ring), date,
        filter_type=3,                 # full single day, so a run can develop
        granularity=GRAN,
        analytic_type=analytic,
        threshold=threshold,
        direction="above",
    )
    vals, stats_data = [], (r or {}).get("stats_data") or {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        v = f.get("properties", {}).get("value")
        if v is not None:
            vals.append(float(v))
    return vals, stats_data


def hist(vals, bins=12, width=42):
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / span * bins))] += 1
    peak = max(counts) or 1
    return "\n".join(
        f"      {lo + span*i/bins:6.2f}h {'#'*int(width*n/peak):<{width}} {n}"
        for i, n in enumerate(counts))


def describe(name, vals):
    vals_sorted = sorted(vals)
    n = len(vals_sorted)
    return {
        "n": n,
        "mean": statistics.mean(vals),
        "sd": statistics.pstdev(vals),
        "min": vals_sorted[0],
        "max": vals_sorted[-1],
        "p10": vals_sorted[n // 10],
        "p90": vals_sorted[n * 9 // 10],
        "span": vals_sorted[-1] - vals_sorted[0],
        "iqr": vals_sorted[n * 3 // 4] - vals_sorted[n // 4],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="all", choices=list(CITIES) + ["all"])
    args = ap.parse_args()

    load_dotenv()
    client = NightwatchClient()

    try:
        before = client.usage()
    except Exception:
        before = None

    keys = list(CITIES) if args.city == "all" else [args.city]
    results = {}

    for key in keys:
        cfg = CITIES[key]
        th = THRESHOLDS[key]
        print("\n" + "=" * 72)
        print(f"{cfg['label']}")
        print(f"threshold {th}C  ·  {geo.polygon_area_sq_mi(cfg['ring']):.1f} mi^2"
              f"  ·  filter_type 3 (full day)")
        print("=" * 72)

        row = {}
        for analytic in ("persistence", "exceedance"):
            try:
                vals, sd = fetch_analytic(client, cfg["ring"], cfg["date"],
                                          analytic, th)
            except Exception as e:
                print(f"  {analytic}: FAILED {type(e).__name__}: {e}")
                continue
            if not vals:
                print(f"  {analytic}: no values returned")
                continue
            d = describe(analytic, vals)
            row[analytic] = d
            print(f"\n  {analytic.upper()}   units: {sd.get('units','?')}")
            print(f"    n {d['n']}   mean {d['mean']:5.2f}h   sd {d['sd']:5.2f}h")
            print(f"    min {d['min']:5.2f}   p10 {d['p10']:5.2f}   "
                  f"p90 {d['p90']:5.2f}   max {d['max']:5.2f}")
            print(f"    SPAN {d['span']:5.2f}h   IQR {d['iqr']:5.2f}h")
            print(hist(vals))
        results[key] = row

    # ---------------------------------------------------------------
    print("\n" + "=" * 72)
    print("TEMPERATURE vs DURATION — does duration discriminate?")
    print("=" * 72)
    print(f"  {'city':<9} {'temp sd':>8} {'persist sd':>11} {'persist span':>13} "
          f"{'exceed sd':>10}")
    for key in keys:
        r = results.get(key, {})
        p, e = r.get("persistence"), r.get("exceedance")
        print(f"  {key:<9} {TEMP_SD.get(key, float('nan')):7.3f}C "
              f"{(p['sd'] if p else float('nan')):10.2f}h "
              f"{(p['span'] if p else float('nan')):12.2f}h "
              f"{(e['sd'] if e else float('nan')):9.2f}h")

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    good = [k for k in keys
            if results.get(k, {}).get("persistence")
            and results[k]["persistence"]["iqr"] >= 1.0]
    flat = [k for k in keys
            if results.get(k, {}).get("persistence")
            and results[k]["persistence"]["span"] < 1.0]

    if good:
        print(f"  Persistence discriminates in: {', '.join(good)}")
        print("  (IQR >= 1 hour between the middle 50% of blocks)")
        print("\n  => DURATION IS THE VARIABLE. Rebuild the map on persistence")
        print("     rather than temperature. This is the original NightWatch")
        print("     design, it matches FortyGuard's own published finding, and")
        print("     it is the physiologically correct measure -- sustained")
        print("     exposure denies recovery, the peak does not.")
        print("\n  Next: re-run the day/night ranking test using persistence")
        print("        at a night threshold, and check whether the blocks it")
        print("        flags differ from the ones an afternoon map flags.")
    elif flat:
        print(f"  Persistence is nearly constant in: {', '.join(flat)}")
        print("  => Duration does not discriminate either, at this threshold.")
        print("     Before concluding, retry with a threshold nearer each")
        print("     city's night mean -- if everything clears the threshold")
        print("     for the same number of hours, the threshold is wrong,")
        print("     not the metric.")
    else:
        print("  Mixed. Read the IQRs above: an IQR near zero means every")
        print("  block behaves identically and there is nothing to map.")

    if before is not None:
        try:
            after = client.usage()
            print(f"\n  usage before: {before}")
            print(f"  usage after : {after}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
