#!/usr/bin/env python3
"""Get street-level photos from inside the persistently-hottest area.

sv_probe.py settled the question: /streetview works — three landmark
locations returned images in 5-7 seconds — but the core's geometric centroid
came back 'Failed'. That centroid sits in an industrial patch with no public
road, and Street View needs a road.

So instead of asking at the centre of the area, ask at blocks inside it that
are likely to be on a street, and keep trying until one has coverage.
Failures are free, so the search costs nothing; only the photos are charged.

Two rules it follows:

  * candidates are spread across the whole core by farthest-point sampling,
    not taken from wherever the list happens to start, so the photo is
    representative of the area rather than of one corner.

  * once a location works, all four headings are taken THERE. Four views of
    one place is more use in a report than four unrelated snapshots.

Cost: 4 x 8,600 = 34,400 credits once a location is found. The search itself
is free.

    python core_photos.py --city houston
    python core_photos.py --city houston --tries 20
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch.client import NightwatchClient  # noqa: E402
from city_test import CITIES  # noqa: E402
from explain_core import farthest_point_sample  # noqa: E402
from multidate import km  # noqa: E402

DATA = Path("web/data")
HEADINGS = [0, 90, 180, 270]
SV_COST = 8_600
FIND_TIMEOUT = 60      # a location either has imagery or it does not
PHOTO_TIMEOUT = 180


def try_here(client, lat, lon, timeout):
    r = client.streetview(lat, lon, horizontal_angle=0.0,
                          vertical_angle=15.0, timeout_s=timeout)
    front = (r or {}).get("front") or {}
    return front if front.get("original_image") else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="houston", choices=list(CITIES))
    ap.add_argument("--tries", type=int, default=14,
                    help="candidate blocks to test before giving up")
    args = ap.parse_args()

    load_dotenv()
    cfg = CITIES[args.city]
    cons_path = DATA / f"consistency_{args.city}.json"
    if not cons_path.exists():
        sys.exit(f"{cons_path} missing — run consistency.py first")
    cons = json.loads(cons_path.read_text())
    core = [c for c in cons["cells"] if c[2] >= 0.999]
    if not core:
        sys.exit("no persistently-hottest blocks in this city")

    cx = statistics.mean(c[0] for c in core)
    cy = statistics.mean(c[1] for c in core)
    print(f"{cfg['label']}")
    print(f"  {len(core)} blocks hottest on every heat event")
    print(f"  searching up to {args.tries} of them for street coverage")
    print(f"  (failures are free — only photos are charged)\n")

    client = NightwatchClient(use_cache=False)
    candidates = farthest_point_sample(core, args.tries)
    # nearest the centre first: more likely to represent the core
    candidates.sort(key=lambda c: km((cx, cy), (c[0], c[1])))

    found = None
    for i, c in enumerate(candidates, 1):
        lon, lat = c[0], c[1]
        d = km((cx, cy), (lon, lat))
        print(f"  {i:2d}. {lat:.4f}, {lon:.4f}  ({d:.1f} km from centre) ... ",
              end="", flush=True)
        try:
            front = try_here(client, lat, lon, FIND_TIMEOUT)
        except Exception as e:
            print(f"no  ({type(e).__name__})")
            continue
        if not front:
            print("no image")
            continue
        print(f"YES  imagery from {front.get('image_date')}")
        found = (lat, lon)
        break

    if not found:
        print(f"\n  None of {len(candidates)} blocks had coverage. The core is")
        print("  largely off the road network — itself worth stating, since a")
        print("  place with no street imagery is usually industrial land.")
        print("  Nothing was charged.")
        return

    lat, lon = found
    print(f"\n  Taking four headings at {lat:.4f}, {lon:.4f}")
    print(f"  {len(HEADINGS)} x {SV_COST:,} = {len(HEADINGS)*SV_COST:,} credits\n")

    sv, fails = [], 0
    for hdg in HEADINGS:
        try:
            r = client.streetview(lat, lon, horizontal_angle=float(hdg),
                                  vertical_angle=15.0, timeout_s=PHOTO_TIMEOUT)
        except Exception as e:
            fails += 1
            print(f"    {hdg:3d}deg failed: {type(e).__name__}")
            if fails >= 2:
                print("    two failures in a row — stopping")
                break
            continue
        front = (r or {}).get("front") or {}
        entry = {"heading": hdg, "lat": lat, "lon": lon,
                 "segments": front.get("segments") or {},
                 "image_date": front.get("image_date")}
        for key, tag in (("original_image", "photo"),
                         ("segmented_image", "mask")):
            b64 = front.get(key)
            if not b64:
                continue
            try:
                blob = base64.b64decode(b64.split(",")[-1])
            except Exception:
                continue
            fn = DATA / f"sv_{args.city}_{hdg}_{tag}.png"
            fn.write_bytes(blob)
            entry[tag] = fn.name
            print(f"    {hdg:3d}deg -> {fn.name} ({len(blob)/1024:.0f} KB)")
        segs = entry["segments"]
        if segs:
            top = sorted(segs.items(), key=lambda x: -x[1])[:4]
            print("          " + ", ".join(f"{k} {v:.0f}%" for k, v in top))
        sv.append(entry)

    # fold into the existing explain file rather than replacing it
    out_path = DATA / f"explain_{args.city}.json"
    out = json.loads(out_path.read_text()) if out_path.exists() else {
        "city": args.city, "label": cfg["label"]}
    out["streetview"] = sv
    out["streetview_note"] = (
        f"Photographed at {lat:.4f}, {lon:.4f} — a block inside the "
        f"persistently-hottest area that has street coverage. The area's "
        f"geometric centroid returned no imagery, which is itself a clue: it "
        f"is not on a public road.")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n  wrote {out_path}")

    try:
        rem = client.usage()["credit_summary"]["cycle_remaining_credits"]
        print(f"  credits remaining: {rem:,}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
