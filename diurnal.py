#!/usr/bin/env python3
"""Draw the day, so the hours figure stops being an abstraction.

Every "hours above the threshold" number in this project comes from one
FortyGuard call over a full 24 hours. Nothing on the site showed WHEN in
those 24 hours the hot stretch actually falls, which let the copy drift
into calling it night-time heat. It is not: the stretch runs from
mid-morning until a couple of hours after sunset.

This reads the 24-hour tcm sweep already sitting in the disk cache, so it
costs nothing, and writes an SVG of the curve with the threshold drawn
across it and the two crossing points marked.

    python diurnal.py
"""
from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

CACHE = Path("data/cache.sqlite")
OUT = Path("web/img/diurnal.svg")
THRESHOLD = 25.0          # Chicago, the city the cached sweep covers
SUNSET = 20.33            # 20:20 local, Chicago, 22 July


def series_from_cache():
    """tile-mean temperature per hour, for the AOI with a full 24 h sweep."""
    db = sqlite3.connect(CACHE)
    rows = db.execute("SELECT request_json, result_json FROM responses").fetchall()
    aoi = collections.defaultdict(dict)
    for req, res in rows:
        try:
            q, r = json.loads(req), json.loads(res)
        except Exception:
            continue
        if q.get("analytic_type") != "tcm":
            continue
        st = (q.get("date_time") or {}).get("start_time")
        if not st:
            continue
        temps = [f["properties"]["average_temperature"]
                 for f in (r or {}).get("map_data", {}).get("features", [])
                 if f.get("properties", {}).get("average_temperature") is not None]
        if not temps:
            continue
        key = hashlib.sha1(
            json.dumps(q.get("polygon_aoi"), sort_keys=True).encode()).hexdigest()[:8]
        aoi[key][int(st[:2])] = (sum(temps) / len(temps), len(temps))
    full = {k: v for k, v in aoi.items() if len(v) == 24}
    if not full:
        sys.exit("no full 24 h sweep in the cache; run hourly_curve.py first")
    # the biggest AOI, so the mean is the least noisy
    key = max(full, key=lambda k: full[k][0][1])
    return key, {h: v[0] for h, v in full[key].items()}, full[key][0][1]


def crossings(curve, th):
    """(first hour above, last hour above) for the run containing the peak."""
    peak = max(curve, key=lambda h: curve[h])
    if curve[peak] <= th:
        return None
    s = e = peak
    while s - 1 >= 0 and curve[s - 1] > th:
        s -= 1
    while e + 1 <= 23 and curve[e + 1] > th:
        e += 1
    return s, e


def build_svg(curve, th, n_tiles):
    W, H = 720, 300
    L, R, T, B = 46, 16, 18, 42
    iw, ih = W - L - R, H - T - B
    lo = min(min(curve.values()), th) - 1.5
    hi = max(max(curve.values()), th) + 1.5
    X = lambda h: L + iw * h / 23
    Y = lambda t: T + ih - ih * (t - lo) / (hi - lo)

    s, e = crossings(curve, th)
    p = []

    # night shading, so "after dark" is visible rather than asserted
    p.append(f'<rect x="{X(SUNSET):.1f}" y="{T}" width="{X(23)-X(SUNSET):.1f}" '
             f'height="{ih}" fill="#0e1a2b"/>')
    p.append(f'<rect x="{L}" y="{T}" width="{X(6)-L:.1f}" height="{ih}" '
             f'fill="#0e1a2b"/>')

    # the run above the threshold
    p.append(f'<rect x="{X(s):.1f}" y="{T}" width="{X(e)-X(s):.1f}" height="{ih}" '
             f'fill="#f9731622"/>')

    # threshold line
    p.append(f'<line x1="{L}" y1="{Y(th):.1f}" x2="{L+iw}" y2="{Y(th):.1f}" '
             f'stroke="#f97316" stroke-width="1.5" stroke-dasharray="5 4"/>')
    p.append(f'<text x="{L+iw}" y="{Y(th)-7:.1f}" text-anchor="end" font-size="11" '
             f'fill="#f97316" font-weight="600">danger threshold {th:g} °C</text>')

    # the curve
    pts = " ".join(f"{X(h):.1f},{Y(curve[h]):.1f}" for h in sorted(curve))
    p.append(f'<polyline points="{pts}" fill="none" stroke="#7dd3fc" '
             f'stroke-width="2.5" stroke-linejoin="round"/>')

    # crossing markers
    for h, lbl in ((s, "crosses up"), (e, "drops back")):
        p.append(f'<circle cx="{X(h):.1f}" cy="{Y(curve[h]):.1f}" r="4" '
                 f'fill="#0a0e14" stroke="#f97316" stroke-width="2"/>')
        p.append(f'<text x="{X(h):.1f}" y="{T-5}" text-anchor="middle" '
                 f'font-size="10.5" fill="#f97316">{lbl} {h:02d}:00</text>')

    # sunset
    p.append(f'<line x1="{X(SUNSET):.1f}" y1="{T}" x2="{X(SUNSET):.1f}" '
             f'y2="{T+ih}" stroke="#64748b" stroke-width="1" stroke-dasharray="3 3"/>')
    p.append(f'<text x="{X(SUNSET)+5:.1f}" y="{T+13}" font-size="10.5" '
             f'fill="#94a3b8">sunset</text>')

    # axes
    for h in range(0, 24, 3):
        p.append(f'<text x="{X(h):.1f}" y="{T+ih+17}" text-anchor="middle" '
                 f'font-size="10.5" fill="#8792a5">{h:02d}:00</text>')
    for t in range(int(lo) + 1, int(hi) + 1, 3):
        p.append(f'<text x="{L-8}" y="{Y(t)+4:.1f}" text-anchor="end" '
                 f'font-size="10.5" fill="#8792a5">{t}°</text>')
    p.append(f'<text x="{L+iw/2:.1f}" y="{H-8}" text-anchor="middle" '
             f'font-size="11" fill="#c3ccda">local time</text>')

    body = "\n  ".join(p)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="100%" role="img" aria-label="Temperature through 24 hours '
            f'in Chicago, crossing the {th:g} degree threshold at {s:02d}:00 and '
            f'dropping back at {e:02d}:00">\n  {body}\n</svg>\n'), s, e


def main():
    key, curve, n = series_from_cache()
    svg, s, e = build_svg(curve, THRESHOLD, n)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    print(f"AOI {key}, {n} tiles, mean of every tile")
    print(f"  above {THRESHOLD} °C from {s:02d}:00 to {e:02d}:00 = {e-s+1} hours")
    print(f"  sunset ~20:20, so about {max(0, e-20):.0f} of those hours are after dark")
    print(f"  at 03:00 the mean is {curve[3]:.2f} °C, "
          f"{THRESHOLD-curve[3]:.2f} °C below the threshold")
    print(f"  wrote {OUT} ({OUT.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
