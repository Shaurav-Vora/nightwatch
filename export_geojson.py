#!/usr/bin/env python3
"""Step 7: turn the cached tiles into GeoJSON the frontend can render.

Zero credits. Everything here is already paid for and sitting in the cache.

Why the metric changed
----------------------
The first version ranked tiles by COOLING DEFICIT (day minus night) and
called the smallest values "worst". That is wrong, and the earlier output
showed why: the top "worst coolers" were 24C at 3pm -- the coldest tiles in
the city. A block that was never hot does not cool much either, so it scored
as dangerous while being nothing of the sort.

What actually harms people is ABSOLUTE NIGHT TEMPERATURE. The body needs air
to fall below a recovery threshold overnight; it does not care what the
afternoon looked like. That is also what ETCCDI encodes in its tropical-
nights family of indices.

So the hero variable is now NIGHT HEAT RETENTION:

    night_resid = T(03:00) - plane(lon, lat)

i.e. how much warmer this block is at 3am than its POSITION alone predicts.
Fitting and subtracting the lon/lat plane removes the lake-breeze gradient,
so what remains is the part attributable to the block itself. A tile that is
warm at night merely because it sits near Lake Michigan scores zero; a tile
that is warm at night despite being deep inland scores high. That is the
urban heat island, position-controlled.

Three exclusion tiers, because the lake contaminates everything:
    water        cooling <= 5C -- a different physical regime entirely
    coastal      within 1 km of water -- lake-influenced, distorts the fit
    deep inland  more than 3 km from water -- the strictest control, used to
                 test whether the day/night reversal survives with the lake
                 removed as far as the AOI allows

Emits:
    web/data/chicago.geojson
    web/data/chicago.json
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch import geo  # noqa: E402
from nightwatch.client import NightwatchClient  # noqa: E402
from cooling_deficit import (  # noqa: E402
    AOI_RING, DAY_HOUR, GRAN, NIGHT_HOUR, PROBE_DATE, spearman,
)
from detrend import ols2  # noqa: E402

OUT_DIR = Path("web/data")
CITY = "chicago"

WATER_COOLING_MAX = 5.0   # C shed overnight; below this it behaves as water
COASTAL_KM = 1.0          # lake-influenced fringe
DEEP_INLAND_KM = 3.0      # strict control


def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def round_ring(ring: list, dp: int = 5) -> list:
    return [[round(float(x), dp), round(float(y), dp)] for x, y in ring]


def raw_features(client: NightwatchClient, hour: int) -> dict:
    r = client.heatmap(
        geo.feature_collection(AOI_RING), PROBE_DATE,
        filter_type=1, start_time=f"{hour:02d}:00",
        granularity=GRAN, analytic_type="tcm",
    )
    out = {}
    for f in (r or {}).get("map_data", {}).get("features", []):
        p = f.get("properties", {})
        if p.get("tile_id") is not None and p.get("average_temperature") is not None:
            out[p["tile_id"]] = f
    return out


def distances_to_water(rows: list, water: list) -> None:
    """Set r['dist_km'] = distance to the nearest water tile.

    Bucketed by latitude so this stays fast without numpy: comparing every
    tile against every water tile would be ~14M operations.
    """
    if not water:
        for r in rows:
            r["dist_km"] = 99.0
        return

    BIN = 0.005  # ~0.55 km of latitude
    buckets: dict = {}
    for w in water:
        buckets.setdefault(int(w["lat"] / BIN), []).append(w)

    mean_lat = statistics.mean(r["lat"] for r in rows)
    kx = 111.32 * math.cos(math.radians(mean_lat))
    ky = 111.32

    for r in rows:
        if r["is_water"]:
            r["dist_km"] = 0.0
            continue
        b = int(r["lat"] / BIN)
        best = 9e9
        span = 1
        while True:
            cands = []
            for k in range(b - span, b + span + 1):
                cands.extend(buckets.get(k, ()))
            for w in cands:
                dx = (r["lon"] - w["lon"]) * kx
                dy = (r["lat"] - w["lat"]) * ky
                d = dx * dx + dy * dy
                if d < best:
                    best = d
            # widen until the search radius exceeds the band we have covered
            if cands and math.sqrt(best) <= span * BIN * ky:
                break
            span += 1
            if span > 40:
                break
        r["dist_km"] = round(math.sqrt(best), 3)


def main() -> None:
    load_dotenv()
    client = NightwatchClient()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("loading cached tiles ...")
    day = raw_features(client, DAY_HOUR)
    night = raw_features(client, NIGHT_HOUR)
    shared = sorted(set(day) & set(night))
    print(f"  {len(shared)} tiles")

    rows = []
    for tid in shared:
        ring = (day[tid].get("geometry", {}).get("coordinates") or [[]])[0]
        if not ring:
            continue
        d = float(day[tid]["properties"]["average_temperature"])
        n = float(night[tid]["properties"]["average_temperature"])
        rows.append({
            "tid": tid, "ring": ring,
            "lon": sum(c[0] for c in ring) / len(ring),
            "lat": sum(c[1] for c in ring) / len(ring),
            "d": d, "n": n, "cool": d - n,
            "is_water": (d - n) <= WATER_COOLING_MAX,
        })

    water = [r for r in rows if r["is_water"]]
    distances_to_water(rows, water)
    for r in rows:
        r["is_coastal"] = (not r["is_water"]) and r["dist_km"] < COASTAL_KM
        r["deep_inland"] = r["dist_km"] > DEEP_INLAND_KM

    clean = [r for r in rows if not r["is_water"] and not r["is_coastal"]]
    deep = [r for r in rows if r["deep_inland"]]
    print(f"  {len(water)} water | {sum(r['is_coastal'] for r in rows)} coastal "
          f"| {len(clean)} clean inland | {len(deep)} deep inland (>{DEEP_INLAND_KM}km)")

    # --- fit both planes on CLEAN INLAND tiles -------------------------
    clon = [r["lon"] for r in clean]
    clat = [r["lat"] for r in clean]
    ca, cb, cc, cr2 = ols2(clon, clat, [r["cool"] for r in clean])
    na, nb, nc, nr2 = ols2(clon, clat, [r["n"] for r in clean])
    print(f"  cooling plane R^2 = {cr2:.3f}   night-temp plane R^2 = {nr2:.3f}")

    for r in rows:
        r["cool_resid"] = r["cool"] - (ca * r["lon"] + cb * r["lat"] + cc)
        # THE HERO: warmer at 3am than position alone predicts
        r["night_resid"] = r["n"] - (na * r["lon"] + nb * r["lat"] + nc)

    feats = [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [round_ring(r["ring"])]},
        "properties": {
            "id": r["tid"],
            "day_c": round(r["d"], 2), "night_c": round(r["n"], 2),
            "day_f": round(c_to_f(r["d"]), 1), "night_f": round(c_to_f(r["n"]), 1),
            "cooling_c": round(r["cool"], 2),
            "residual_c": round(r["cool_resid"], 3),
            "night_resid_c": round(r["night_resid"], 3),
            "dist_km": r["dist_km"],
            "is_water": r["is_water"], "is_coastal": r["is_coastal"],
            "deep_inland": r["deep_inland"],
        },
    } for r in rows]

    gj = OUT_DIR / f"{CITY}.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                             separators=(",", ":")))
    print(f"  wrote {gj}  ({gj.stat().st_size / 1e6:.1f} MB)")

    # --- statistics ----------------------------------------------------
    def overlap(pool: list, k: int = 20) -> dict:
        if len(pool) < k * 4:
            return {}
        hot_day = {r["tid"] for r in sorted(pool, key=lambda r: -r["d"])[:k]}
        hot_night = {r["tid"] for r in sorted(pool, key=lambda r: -r["n"])[:k]}
        worst_ret = {r["tid"] for r in
                     sorted(pool, key=lambda r: -r["night_resid"])[:k]}
        return {
            "k": k,
            "day_and_night": len(hot_day & hot_night),
            "day_and_retention": len(hot_day & worst_ret),
            "rho": round(spearman([r["d"] for r in pool],
                                  [r["n"] for r in pool]), 3),
        }

    ov_clean, ov_deep = overlap(clean), overlap(deep)
    nres = [r["night_resid"] for r in clean]
    cres = [r["cool_resid"] for r in clean]
    ln = [r["n"] for r in clean]
    ld = [r["d"] for r in clean]
    lc = [r["cool"] for r in clean]

    stats = {
        "city": "Chicago, IL", "date": PROBE_DATE,
        "day_hour": DAY_HOUR, "night_hour": NIGHT_HOUR,
        "granularity_m": GRAN,
        "aoi_sq_mi": round(geo.polygon_area_sq_mi(AOI_RING), 1),
        "n_tiles": len(rows), "n_water": len(water),
        "n_coastal": sum(r["is_coastal"] for r in rows),
        "n_clean": len(clean), "n_deep": len(deep),
        "clean": ov_clean, "deep": ov_deep,
        "rho_all": round(spearman([r["d"] for r in rows],
                                  [r["n"] for r in rows]), 3),
        "spans": {
            "day_c": round(max(ld) - min(ld), 2),
            "night_c": round(max(ln) - min(ln), 2),
            "cooling_c": round(max(lc) - min(lc), 2),
            "night_resid_c": round(max(nres) - min(nres), 2),
        },
        "night_retention": {
            "sd": round(statistics.pstdev(nres), 3),
            "min": round(min(nres), 2), "max": round(max(nres), 2),
            "plane_r2": round(nr2, 3),
        },
        "cooling_residual": {
            "sd": round(statistics.pstdev(cres), 3),
            "plane_r2": round(cr2, 3),
        },
    }
    (OUT_DIR / f"{CITY}.json").write_text(json.dumps(stats, indent=2))

    # --- console verdict ------------------------------------------------
    print("\n" + "=" * 66)
    print("DOES THE REVERSAL SURVIVE AWAY FROM THE LAKE?")
    print("=" * 66)
    for label, ov, n in (("clean inland (>1km)", ov_clean, len(clean)),
                         (f"deep inland (>{DEEP_INLAND_KM}km)", ov_deep, len(deep))):
        if not ov:
            print(f"  {label:<24} too few tiles ({n})")
            continue
        print(f"  {label:<24} n={n:5d}  rho={ov['rho']:+.3f}  "
              f"top20 day&night overlap={ov['day_and_night']}/20")
    if ov_deep and ov_clean:
        if ov_deep["rho"] < -0.25:
            print("\n  => The reversal survives with the lake excluded to 3 km.")
            print("     This is a real urban finding, not a shoreline artefact.")
        elif ov_deep["rho"] > -0.1:
            print("\n  => The reversal COLLAPSES deep inland. What is left is")
            print("     the lake. Do not claim an urban reversal; report the")
            print("     lake-breeze finding honestly instead -- it is still a")
            print("     real and useful result about where heat lingers.")
        else:
            print("\n  => Weakened but present. Lead with the deep-inland number.")

    print(f"\n  night heat retention: sd {stats['night_retention']['sd']}C, "
          f"span {stats['spans']['night_resid_c']}C")
    print(f"  night-temp plane R^2 {nr2:.3f} "
          f"({nr2*100:.0f}% of night temperature is position alone)")


if __name__ == "__main__":
    main()
