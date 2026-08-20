#!/usr/bin/env python3
"""Task 2: export all three cities for the frontend, with exposure as hero.

Zero credits -- every fetch below is a cache hit.

Supersedes export_geojson.py, which exported Chicago only and was built
around cooling deficit. That metric is retired: it rewarded blocks for never
having been hot. The hero is now EXPOSURE -- hours above a danger threshold,
from analytic_type "persistence" (the longest UNBROKEN run, not scattered
totals, because 8 consecutive dangerous hours is an event and 8 scattered
ones are an inconvenience).

Compact output format
---------------------
Three cities at 100 m is ~53,000 tiles. As GeoJSON polygons that is roughly
20 MB, which is too heavy for a web page. Every tile is an identical square
on a regular grid, so emitting one centroid plus a shared cell size is
about a tenth of the size and the frontend rebuilds the squares. Phoenix
drops from ~8 MB to under 1 MB.

    web/data/cities.json     index + statistics for all three
    web/data/<city>.json     the tiles

    python export_cities.py
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
from city_test import CITIES  # noqa: E402
from duration_test import THRESHOLDS  # noqa: E402
from amplification import fetch_tcm, fetch_persistence, ols1  # noqa: E402

OUT = Path("web/data")
GRAN = 100
DAY_HOUR, NIGHT_HOUR = 15, 3
WATER_COOLING_MAX = 5.0

FIELDS = ["lon", "lat", "exposure_h", "day_c", "night_c", "is_water"]


def cell_size(ring: list) -> tuple:
    """dlon, dlat of one tile, taken from an actual returned polygon."""
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    return round(max(lons) - min(lons), 6), round(max(lats) - min(lats), 6)


def fetch_geometry(client, ring, date, hour):
    """tile_id -> the polygon ring, so we can measure one cell."""
    r = client.heatmap(
        geo.feature_collection(ring), date, filter_type=1,
        start_time=f"{hour:02d}:00", granularity=GRAN, analytic_type="tcm",
    )
    for f in (r or {}).get("map_data", {}).get("features", []):
        g = (f.get("geometry", {}).get("coordinates") or [[]])[0]
        if g:
            return g
    return None


def main() -> None:
    load_dotenv()
    client = NightwatchClient()
    OUT.mkdir(parents=True, exist_ok=True)

    index = {"cities": [], "generated": None}
    print("=" * 70)
    print("TASK 2 — EXPORTING THREE CITIES, EXPOSURE AS HERO")
    print("zero credits, cache only")
    print("=" * 70)

    for key, cfg in CITIES.items():
        th = THRESHOLDS[key]
        print(f"\n{key}")

        try:
            day = fetch_tcm(client, cfg["ring"], cfg["date"], DAY_HOUR)
            night = fetch_tcm(client, cfg["ring"], cfg["date"], NIGHT_HOUR)
            expo = fetch_persistence(client, cfg["ring"], cfg["date"], th)
            sample_ring = fetch_geometry(client, cfg["ring"], cfg["date"],
                                         DAY_HOUR)
        except Exception as e:
            print(f"  cache miss ({type(e).__name__}) -- skipping")
            continue

        ids = sorted(set(day) & set(night) & set(expo))
        if not ids or not sample_ring:
            print("  no overlapping tiles; skipping")
            continue

        dlon, dlat = cell_size(sample_ring)
        cells = []
        for i in ids:
            d, lon, lat = day[i]
            n = night[i][0]
            cells.append([
                round(lon, 5), round(lat, 5),
                round(expo[i], 2), round(d, 2), round(n, 2),
                1 if (d - n) <= WATER_COOLING_MAX else 0,
            ])

        e = [c[2] for c in cells]
        d = [c[3] for c in cells]
        n = [c[4] for c in cells]
        land = [c for c in cells if not c[5]]
        le = [c[2] for c in land] or e
        ld = [c[3] for c in land] or d

        slope, _, r2 = ols1(ld, le)

        # --- the twin pair --------------------------------------------
        # Two blocks with near-identical afternoon temperature and the
        # largest possible gap in exposure. This is the finding made
        # concrete: a statistic nobody feels, turned into two addresses.
        twins = None
        by_temp = sorted(land, key=lambda c: c[3])
        best_gap = 0.0
        for i, a in enumerate(by_temp):
            for b in by_temp[i + 1:]:
                if b[3] - a[3] > 0.05:       # same afternoon temp, +/-0.05C
                    break
                gap = abs(a[2] - b[2])
                if gap > best_gap:
                    best_gap, twins = gap, (a, b)
        twin_out = None
        if twins:
            a, b = sorted(twins, key=lambda c: c[2])
            twin_out = {
                "temp_c": round((a[3] + b[3]) / 2, 2),
                "gap_h": round(b[2] - a[2], 2),
                "low":  {"lon": a[0], "lat": a[1], "exposure_h": a[2],
                         "day_c": a[3], "night_c": a[4]},
                "high": {"lon": b[0], "lat": b[1], "exposure_h": b[2],
                         "day_c": b[3], "night_c": b[4]},
            }

        # --- 2D density for the scatter chart -------------------------
        # 21k points would be wasteful to ship and slow to draw; a binned
        # density is compact and reads better anyway.
        NX, NY = 44, 30
        x0, x1 = min(ld), max(ld)
        y0, y1 = min(le), max(le)
        grid = [[0] * NX for _ in range(NY)]
        for c in land:
            gx = min(NX - 1, int((c[3] - x0) / ((x1 - x0) or 1) * NX))
            gy = min(NY - 1, int((c[2] - y0) / ((y1 - y0) or 1) * NY))
            grid[gy][gx] += 1

        payload = {
            "city": key,
            "label": cfg["label"],
            "date": cfg["date"],
            "threshold_c": th,
            "granularity_m": GRAN,
            "day_hour": DAY_HOUR, "night_hour": NIGHT_HOUR,
            "cell": [dlon, dlat],
            "fields": FIELDS,
            "n": len(cells),
            "n_water": sum(c[5] for c in cells),
            "twins": twin_out,
            "scatter": {"nx": NX, "ny": NY,
                        "x0": round(x0, 2), "x1": round(x1, 2),
                        "y0": round(y0, 2), "y1": round(y1, 2),
                        "grid": grid},
            "cells": cells,
        }
        # population.py appends a "people" column to this same file. Writing
        # it fresh silently destroys that, and the only symptom is the
        # frontend quietly saying "no census data" — which is exactly what
        # happened. Carry it across instead.
        p = OUT / f"{key}.json"
        if p.exists():
            try:
                old = json.loads(p.read_text())
                if "people" in (old.get("fields") or []):
                    idx = old["fields"].index("people")
                    prev = {f"{c[0]},{c[1]}": c[idx] for c in old["cells"]
                            if len(c) > idx}
                    hit = 0
                    for c in payload["cells"]:
                        v = prev.get(f"{c[0]},{c[1]}")
                        if v is not None:
                            c.append(v)
                            hit += 1
                        else:
                            c.append(0.0)
                    if hit:
                        payload["fields"] = payload["fields"] + ["people"]
                        if old.get("population"):
                            payload["population"] = old["population"]
                        print(f"  carried population across for {hit} of "
                              f"{len(payload['cells'])} blocks")
                    else:
                        for c in payload["cells"]:
                            c.pop()
            except Exception as e:
                print(f"  could not preserve population ({type(e).__name__}) "
                      f"— re-run population.py")

        p.write_text(json.dumps(payload, separators=(",", ":")))

        stats = {
            "city": key,
            "label": cfg["label"],
            "short": cfg["label"].split(",")[0],
            "date": cfg["date"],
            "threshold_c": th,
            "aoi_sq_mi": round(geo.polygon_area_sq_mi(cfg["ring"]), 1),
            "n": len(cells),
            "n_water": sum(c[5] for c in cells),
            "temp_span_c": round(max(ld) - min(ld), 2),
            "temp_sd_c": round(statistics.pstdev(ld), 3),
            "exposure_span_h": round(max(le) - min(le), 2),
            "exposure_sd_h": round(statistics.pstdev(le), 3),
            "exposure_mean_h": round(statistics.mean(le), 2),
            "slope_h_per_c": round(slope, 2),
            "r2": round(r2, 3),
        }
        index["cities"].append(stats)

        print(f"  {len(cells)} tiles ({stats['n_water']} water)  "
              f"cell {dlon}x{dlat} deg")
        print(f"  temp span {stats['temp_span_c']}C -> "
              f"exposure span {stats['exposure_span_h']}h   "
              f"R2 {stats['r2']}")
        print(f"  wrote {p} ({p.stat().st_size/1e6:.2f} MB)")

    # order: the strongest demonstration first
    index["cities"].sort(key=lambda c: c["r2"])
    (OUT / "cities.json").write_text(json.dumps(index, indent=2))
    print(f"\nwrote {OUT/'cities.json'} — "
          f"{len(index['cities'])} cities, default "
          f"{index['cities'][0]['city'] if index['cities'] else 'none'}")


if __name__ == "__main__":
    main()
