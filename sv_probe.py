#!/usr/bin/env python3
"""Is /streetview broken, or is there just no imagery where we asked?

explain_core.py timed out on both headings at the Houston core centroid
(29.7568, -95.3053). That area is industrial — heavy on roads and "field",
0.84x the city's residential density — so the centroid of an irregular patch
could easily sit in a rail yard or a lot with no public road. Street View
needs a road. A failure there says nothing about the endpoint.

This tries the same call at places that certainly have coverage, so the two
explanations can be told apart:

  broken endpoint   -> everything times out, including a famous intersection
  no local imagery  -> the landmarks work and only the core centroid fails

Costs nothing unless a call SUCCEEDS (8,600 each). Timeouts and failures are
free, which is what makes this worth running.

    python sv_probe.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from nightwatch.client import NightwatchClient  # noqa: E402

# ordered from "certainly has imagery" to "the one that failed"
PLACES = [
    ("Downtown Houston, Main & Dallas", 29.7566, -95.3629),
    ("Houston, Kirby Dr near US-59",    29.7330, -95.4180),
    ("Phoenix, Central & Washington",   33.4479, -112.0740),
    ("The Houston core centroid",       29.7568, -95.3053),
]
TIMEOUT = 240      # far longer than explain_core's 90s, to rule that out


def main():
    load_dotenv()
    client = NightwatchClient(use_cache=False)

    print("=" * 70)
    print("STREET VIEW DIAGNOSTIC")
    print("timeouts and failures cost nothing; only a success is charged")
    print("=" * 70)

    ok = fail = 0
    for name, lat, lon in PLACES:
        print(f"\n{name}  ({lat}, {lon})")
        t0 = time.monotonic()
        try:
            r = client.streetview(lat, lon, horizontal_angle=0.0,
                                  vertical_angle=15.0, timeout_s=TIMEOUT)
        except Exception as e:
            print(f"  FAILED after {time.monotonic()-t0:5.1f}s  "
                  f"{type(e).__name__}: {str(e)[:80]}")
            fail += 1
            continue

        front = (r or {}).get("front") or {}
        segs = front.get("segments") or {}
        has_img = bool(front.get("original_image"))
        print(f"  OK after {time.monotonic()-t0:5.1f}s   "
              f"image: {'yes' if has_img else 'NO'}   "
              f"date: {front.get('image_date')}")
        if segs:
            top = sorted(segs.items(), key=lambda x: -x[1])[:4]
            print("  " + ", ".join(f"{k} {v:.0f}%" for k, v in top))
        ok += 1

    print("\n" + "=" * 70)
    if ok == 0:
        print("  Every location timed out, including ones that certainly have")
        print("  imagery. /streetview is not returning results at all — an")
        print("  endpoint problem, not a coverage problem. Worth reporting to")
        print("  the organisers, and worth stating in the write-up.")
    elif ok and fail:
        print(f"  {ok} succeeded, {fail} failed. The endpoint works; the")
        print("  failures are places without imagery. Pick a photo location")
        print("  on a real street inside the core rather than its centroid.")
    else:
        print("  Everything worked. The earlier failures were the 90s timeout")
        print("  being too short — raise it and re-run explain_core.")
    print(f"\n  {ok} successful call(s) = {ok * 8600:,} credits")


if __name__ == "__main__":
    main()
