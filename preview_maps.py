#!/usr/bin/env python3
"""Render the two landing-page preview maps straight from the shipped data.

These are not mockups and not screenshots. They are the same Phoenix blocks
in web/data/phoenix.json, drawn twice:

    afternoon temperature   (what a conventional heat map shows)
    hours dangerously hot   (what NIGHTWATCH shows)

Side by side they make the argument without a word of explanation: if
afternoon temperature told you where the night-time danger is, the two
pictures would match. In Phoenix they do not, which is R2 = 0.106 made
visible.

Both panels use the app's own colour ramp and a 2nd-98th percentile stretch,
so neither is flattered. The honest caveat is that a percentile stretch gives
BOTH maps full contrast, which hides the fact that one spans 1.8 C and the
other spans 9.1 hours. That is why the caption carries both spans in text.

Zero credits, no network. Reads only what is already committed.

    python preview_maps.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("needs Pillow:  pip install Pillow")

DATA = Path("web/data")
OUT = Path("web/img")
CITY = "phoenix"
SCALE = 3          # pixels per block
LO_PCT, HI_PCT = 2.0, 98.0

# identical to RAMP in web/index.html, so the previews and the app agree
RAMP = [(8, 20, 48), (20, 60, 110), (30, 110, 150), (90, 160, 140),
        (200, 180, 90), (240, 130, 50), (220, 60, 40), (150, 20, 30)]
BG = (10, 14, 20)


def ramp(t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    x = t * (len(RAMP) - 1)
    i = int(x)
    f = x - i
    a = RAMP[i]
    b = RAMP[min(len(RAMP) - 1, i + 1)]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def percentile(sorted_vals: list, pct: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def render(cells, col_index, dlon, dlat, name, invert_y=True):
    """One PNG, one block per SCALE x SCALE pixel square."""
    lons = sorted({c[0] for c in cells})
    lats = sorted({c[1] for c in cells})
    # snap each centroid onto the regular grid it came from
    lon0, lat0 = lons[0], lats[0]
    nx = int(round((lons[-1] - lon0) / dlon)) + 1
    ny = int(round((lats[-1] - lat0) / dlat)) + 1

    vals = sorted(c[col_index] for c in cells)
    lo = percentile(vals, LO_PCT)
    hi = percentile(vals, HI_PCT)
    span = (hi - lo) or 1.0

    img = Image.new("RGB", (nx, ny), BG)
    px = img.load()
    placed = 0
    for c in cells:
        gx = int(round((c[0] - lon0) / dlon))
        gy = int(round((c[1] - lat0) / dlat))
        if not (0 <= gx < nx and 0 <= gy < ny):
            continue
        y = (ny - 1 - gy) if invert_y else gy      # north at the top
        px[gx, y] = ramp((c[col_index] - lo) / span)
        placed += 1

    img = img.resize((nx * SCALE, ny * SCALE), Image.NEAREST)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    img.save(path, optimize=True)
    kb = path.stat().st_size / 1024
    print(f"  {name:24} {nx}x{ny} blocks -> {img.width}x{img.height}px  "
          f"{kb:5.1f} KB   drew {placed}/{len(cells)}")
    print(f"    stretch {lo:.2f} to {hi:.2f}   full range "
          f"{vals[0]:.2f} to {vals[-1]:.2f}")
    return path


def load(city: str):
    d = json.loads((DATA / f"{city}.json").read_text(encoding="utf-8"))
    fields = d["fields"]
    cells = [c for c in d["cells"] if not c[fields.index("is_water")]]
    return d, fields, cells


def main() -> None:
    # --- the hero pair: one city, one day, asked two ways ---------------
    d, fields, cells = load(CITY)
    dlon, dlat = d["cell"]
    print(f"{d['label']}  {len(cells)} land blocks at {d['granularity_m']} m")
    render(cells, fields.index("day_c"), dlon, dlat, "preview-temperature.png")
    render(cells, fields.index("exposure_h"), dlon, dlat, "preview-exposure.png")
    print("\nBoth panels: same blocks, same day, same colour ramp.")
    print("They disagree because afternoon temperature does not predict the "
          "night here (R2 0.106).\n")

    # --- the three-city strip, exposure only ---------------------------
    # Each city is stretched over its OWN range, because each was measured
    # against its own threshold. Comparing the colours between these three
    # is meaningless and the caption on the page says so.
    print("three-city strip (exposure, each stretched over its own range):")
    for city in ("phoenix", "houston", "chicago"):
        d, fields, cells = load(city)
        dlon, dlat = d["cell"]
        render(cells, fields.index("exposure_h"), dlon, dlat,
               f"city-{city}.png")
        print(f"    {city} threshold {d['threshold_c']} C, "
              f"{len(cells)} land blocks")


if __name__ == "__main__":
    main()
