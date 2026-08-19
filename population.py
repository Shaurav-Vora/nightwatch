#!/usr/bin/env python3
"""How many people live in the blocks that are always hottest?

Zero API credits. US Census data is free.

Why this exists
---------------
The tool currently ranks areas by size. A city officer does not have a
square-kilometre budget, they have a population to protect. A 22 km^2
industrial zone with 800 residents and a 3 km^2 neighbourhood with 15,000
are not the same priority, and until now the ranking said the opposite.

With population attached, the headline changes from

    "this 22.4 km^2 area runs 2.1 hours hotter than the city median"

to

    "14,000 people live in the blocks that are hottest every single time"

which is the sentence a council votes on.

Method, and its limits
----------------------
Two free Census products, no shapefiles and no geopandas:

  * Gazetteer tract file -- one row per census tract with its land area and
    its centroid. Plain tab-separated text.
  * ACS 5-year table B01003 -- total population per tract, as JSON.

Each 100 m block is assigned to the nearest tract centroid, and the tract's
population is shared out across the blocks assigned to it, weighted by
nothing -- an even split. That is the honest description: it is a
*centroid-nearest* approximation, not a polygon intersection.

What that costs in accuracy: blocks near a tract boundary can be assigned to
the wrong tract, and population is assumed uniform inside a tract when it
plainly is not. For ranking areas of several hundred blocks these errors
average out; for any single block the number is indicative only. The output
says so, and the frontend should too.

    python population.py                # all three cities
    python population.py --city houston
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from city_test import CITIES  # noqa: E402

DATA = Path("web/data")
CACHE = Path("data/census")

GAZ_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
           "2023_Gazetteer/2023_Gaz_tracts_national.zip")
ACS_URL = ("https://api.census.gov/data/2022/acs/acs5"
           "?get=NAME,B01003_001E&for=tract:*&in=state:{st}{key}")
KEY_SIGNUP = "https://api.census.gov/data/key_signup.html"

# state FIPS for the cities we ship
STATE_FIPS = {"phoenix": "04", "houston": "48", "chicago": "17"}


def fetch(url: str, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "nightwatch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if binary else r.read().decode("utf-8")


def load_gazetteer() -> dict:
    """{geoid: (lat, lon, land_sq_km)} for every US tract."""
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / "gaz_tracts.txt"
    if not local.exists():
        print("  downloading Gazetteer tract file (~10 MB) ...")
        blob = fetch(GAZ_URL, binary=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = [n for n in z.namelist() if n.endswith(".txt")][0]
            local.write_bytes(z.read(name))
        print(f"  cached to {local}")

    out = {}
    with local.open(encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in row.items()}
            try:
                out[row["GEOID"]] = (float(row["INTPTLAT"]),
                                     float(row["INTPTLONG"]),
                                     float(row["ALAND"]) / 1e6)
            except (KeyError, ValueError):
                continue
    return out


class NeedsKey(RuntimeError):
    pass


def load_population(state: str) -> dict:
    """{geoid: population} for one state, from ACS 5-year.

    The Census API requires a free key. Without one it answers with an HTML
    "Missing Key" page rather than an error status, so a naive reader caches
    the HTML and then fails to parse it on every subsequent run. Validate
    before writing anything to disk.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / f"acs_{state}.json"

    if not local.exists():
        key = os.environ.get("CENSUS_API_KEY", "").strip()
        print(f"  downloading ACS population for state {state} ...")
        body = fetch(ACS_URL.format(st=state,
                                    key=f"&key={key}" if key else ""))
        head = body.lstrip()[:400].lower()
        if "missing key" in head or "invalid key" in head:
            raise NeedsKey("the Census API rejected the request")
        if not head.startswith("["):
            raise RuntimeError(f"unexpected response: {body[:160]}")
        local.write_text(body)          # only cache something usable

    rows = json.loads(local.read_text())
    hdr, body = rows[0], rows[1:]
    i_pop = hdr.index("B01003_001E")
    i_st, i_co, i_tr = hdr.index("state"), hdr.index("county"), hdr.index("tract")
    out = {}
    for r in body:
        try:
            out[r[i_st] + r[i_co] + r[i_tr]] = int(r[i_pop])
        except (ValueError, TypeError):
            continue
    return out


def km(a, b):
    (lo1, la1), (lo2, la2) = a, b
    kx = 111.32 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot((lo1 - lo2) * kx, (la1 - la2) * 111.32)


def nearest_tracts(cells, tracts, pops):
    """Assign every block to the nearest tract centroid.

    Bucketed by latitude so this stays fast without numpy -- the same trick
    used for distance-to-water, and exact rather than approximate.
    """
    cand = [(g, la, lo, a) for g, (la, lo, a) in tracts.items() if g in pops]
    if not cand:
        return {}, []
    BIN = 0.05
    buckets = defaultdict(list)
    for t in cand:
        buckets[int(t[1] / BIN)].append(t)

    assign = {}
    for idx, c in enumerate(cells):
        lon, lat = c[0], c[1]
        b = int(lat / BIN)
        best, bd, span = None, 9e9, 1
        while True:
            pool = []
            for k in range(b - span, b + span + 1):
                pool.extend(buckets.get(k, ()))
            for t in pool:
                d = km((lon, lat), (t[2], t[1]))
                if d < bd:
                    best, bd = t, d
            if pool and bd <= span * BIN * 111.32:
                break
            span += 1
            if span > 30:
                break
        if best:
            assign[idx] = (best[0], bd)
    return assign, cand


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="all", choices=list(CITIES) + ["all"])
    args = ap.parse_args()

    print("Loading census reference data (free, no API credits)")
    try:
        tracts = load_gazetteer()
    except Exception as e:
        sys.exit(f"could not load the Gazetteer file: {type(e).__name__}: {e}\n"
                 f"download it manually from\n  {GAZ_URL}\n"
                 f"and unzip the .txt into {CACHE / 'gaz_tracts.txt'}")
    print(f"  {len(tracts):,} tracts nationally")

    keys = list(CITIES) if args.city == "all" else [args.city]
    summary = []

    for key in keys:
        path = DATA / f"{key}.json"
        if not path.exists():
            print(f"\n{key}: {path} missing — run export_cities.py first")
            continue
        payload = json.loads(path.read_text())
        cells = payload["cells"]

        print(f"\n{'=' * 66}\n{CITIES[key]['label']}\n{'=' * 66}")
        try:
            pops = load_population(STATE_FIPS[key])
        except NeedsKey:
            print("\n" + "!" * 66)
            print("  The US Census API needs a free key.")
            print("!" * 66)
            print(f"  1. Request one (instant, emailed): {KEY_SIGNUP}")
            print( "  2. Add this line to your .env file:")
            print( "         CENSUS_API_KEY=the_key_they_email_you")
            print( "  3. Run this script again.")
            print( "\n  No API credits involved — this is census data, not "
                   "FortyGuard.")
            sys.exit(1)
        except Exception as e:
            print(f"  ACS download failed: {type(e).__name__}: {e}")
            continue
        print(f"  {len(pops):,} tracts in state {STATE_FIPS[key]}")

        assign, _ = nearest_tracts(cells, tracts, pops)
        if not assign:
            print("  no tracts matched; skipping")
            continue

        # share each tract's population evenly across the blocks nearest it
        per_tract = defaultdict(list)
        for idx, (geoid, _) in assign.items():
            per_tract[geoid].append(idx)

        people = [0.0] * len(cells)
        for geoid, idxs in per_tract.items():
            share = pops.get(geoid, 0) / len(idxs)
            for i in idxs:
                people[i] = share

        dists = [d for _, d in assign.values()]
        total = sum(people)
        print(f"  {len(per_tract):,} tracts cover this AOI")
        print(f"  {total:,.0f} residents across {len(cells):,} blocks")
        print(f"  median distance block -> tract centroid: "
              f"{statistics.median(dists):.2f} km")

        # who lives in the persistently hottest blocks?
        cons_path = DATA / f"consistency_{key}.json"
        persistent_people = None
        if cons_path.exists():
            cons = json.loads(cons_path.read_text())
            keyset = {f"{c[0]},{c[1]}" for c in cons["cells"] if c[2] >= 0.999}
            idxs = [i for i, c in enumerate(cells)
                    if f"{c[0]},{c[1]}" in keyset]
            persistent_people = sum(people[i] for i in idxs)
            print(f"\n  {len(idxs):,} blocks were hottest on all "
                  f"{len(cons['dates'])} hot days")
            print(f"  {persistent_people:,.0f} people live in them "
                  f"({100 * persistent_people / total:.1f}% of the area's "
                  f"residents on {100 * len(idxs) / len(cells):.1f}% of its "
                  f"blocks)")
            if total and len(cells):
                lift = ((persistent_people / total) /
                        (len(idxs) / len(cells))) if len(idxs) else 0
                print(f"  density there is {lift:.2f}x the area average")

        payload["fields"] = payload["fields"] + ["people"]
        for i, c in enumerate(cells):
            c.append(round(people[i], 1))
        payload["population"] = {
            "total": round(total),
            "in_persistent": (round(persistent_people)
                              if persistent_people is not None else None),
            "n_tracts": len(per_tract),
            "median_km_to_tract": round(statistics.median(dists), 3),
            "source": "US Census ACS 2022 5-year B01003 + 2023 Gazetteer tracts",
            "method": ("each block assigned to the nearest tract centroid; "
                       "tract population split evenly across its blocks"),
            "caveat": ("approximation, not a polygon intersection. Blocks near "
                       "a tract boundary may be misassigned and population is "
                       "assumed uniform within a tract. Reliable for ranking "
                       "areas of hundreds of blocks; indicative only for any "
                       "single block."),
        }
        path.write_text(json.dumps(payload, separators=(",", ":")))
        print(f"  wrote {path}")
        summary.append((key, total, persistent_people, len(cells)))

    if summary:
        print("\n" + "=" * 66)
        print("WHO IS AFFECTED")
        print("=" * 66)
        print(f"  {'city':<9} {'residents':>12} {'in hottest-every-time':>22} "
              f"{'share':>7}")
        for k, tot, pp, n in summary:
            if pp is None:
                print(f"  {k:<9} {tot:12,.0f} {'no multi-date data':>22}")
            else:
                print(f"  {k:<9} {tot:12,.0f} {pp:22,.0f} "
                      f"{100 * pp / tot if tot else 0:6.1f}%")
        print("\n  Rank areas by people, not by square kilometres.")


if __name__ == "__main__":
    main()
