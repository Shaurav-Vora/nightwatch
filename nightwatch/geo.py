"""Geometry helpers for NightWatch AOIs.

Deliberately dependency-free: the day-one probe must run after nothing more
than `pip install requests python-dotenv`. Spherical-excess area is accurate
to well under 1% at city scale, which is far more precision than "is this
polygon under the 10 mi^2 cap?" requires.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

EARTH_RADIUS_M = 6_371_008.8
SQ_M_PER_SQ_MI = 2_589_988.110336

Coord = Tuple[float, float]  # (lon, lat) -- GeoJSON order


# --------------------------------------------------------------------------
# area
# --------------------------------------------------------------------------
def polygon_area_sq_m(ring: Sequence[Coord]) -> float:
    """Geodesic area of a closed or unclosed lon/lat ring, in square metres.

    Uses the spherical excess formula. Sign is discarded, so winding order
    does not matter.
    """
    pts = list(ring)
    if len(pts) < 3:
        return 0.0
    if pts[0] != pts[-1]:
        pts.append(pts[0])

    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(pts, pts[1:]):
        total += math.radians(lon2 - lon1) * (
            math.sin(math.radians(lat1)) + math.sin(math.radians(lat2))
        )
    return abs(total) * EARTH_RADIUS_M * EARTH_RADIUS_M / 2.0


def polygon_area_sq_mi(ring: Sequence[Coord]) -> float:
    return polygon_area_sq_m(ring) / SQ_M_PER_SQ_MI


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------
def close_ring(ring: Sequence[Coord]) -> List[Coord]:
    """The API rejects unclosed rings. Enforce first == last, always."""
    pts = [tuple(p) for p in ring]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def bbox_ring(west: float, south: float, east: float, north: float) -> List[Coord]:
    return close_ring(
        [(west, south), (east, south), (east, north), (west, north)]
    )


def feature_collection(ring: Sequence[Coord]) -> dict:
    """Wrap a ring in the FeatureCollection shape /v1/heatmap expects."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(p) for p in close_ring(ring)]],
                },
            }
        ],
    }


def square_aoi(lon: float, lat: float, area_sq_mi: float) -> List[Coord]:
    """A roughly square AOI of the requested area, centred on (lon, lat).

    Used by the probe to binary-search the plan's AOI ceiling.
    """
    side_m = math.sqrt(area_sq_mi * SQ_M_PER_SQ_MI)
    dlat = (side_m / 2) / 111_320.0
    dlon = (side_m / 2) / (111_320.0 * math.cos(math.radians(lat)))
    return bbox_ring(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# --------------------------------------------------------------------------
# transects
# --------------------------------------------------------------------------
def bbox_of(ring: Sequence[Coord]) -> Tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def split_bbox(
    ring: Sequence[Coord], max_area_sq_mi: float
) -> List[List[Coord]]:
    """Split a bbox into a grid of tiles each at or under max_area_sq_mi.

    Returns closed rings. The grid is uniform, so every tile is the same size
    and comfortably under the cap rather than exactly at it -- being at the
    ceiling risks a rejection over floating-point noise.
    """
    west, south, east, north = bbox_of(ring)
    total = polygon_area_sq_mi(bbox_ring(west, south, east, north))
    if total <= max_area_sq_mi:
        return [bbox_ring(west, south, east, north)]

    # aim for ~85% of the cap per tile to leave headroom
    target = max_area_sq_mi * 0.85
    n = math.ceil(math.sqrt(total / target))
    # prefer splitting the longer axis more finely
    span_x = (east - west) * math.cos(math.radians((north + south) / 2))
    span_y = north - south
    if span_x >= span_y:
        nx, ny = n, max(1, math.ceil(total / target / n))
    else:
        ny, nx = n, max(1, math.ceil(total / target / n))

    out: List[List[Coord]] = []
    for i in range(nx):
        for j in range(ny):
            w = west + (east - west) * i / nx
            e = west + (east - west) * (i + 1) / nx
            s = south + (north - south) * j / ny
            nn = south + (north - south) * (j + 1) / ny
            out.append(bbox_ring(w, s, e, nn))
    return out


def transect(
    start: Coord,
    end: Coord,
    width_mi: float,
    max_area_sq_mi: float,
) -> List[List[Coord]]:
    """A corridor from `start` to `end`, chunked into API-sized AOIs.

    This is the core of the harvest strategy: a wedge from dense core out to
    open land, rather than an exhaustive city grid. See the spec, section 3.
    """
    (lon1, lat1), (lon2, lat2) = start, end
    mid_lat = (lat1 + lat2) / 2
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))

    dx = (lon2 - lon1) * m_per_deg_lon
    dy = (lat2 - lat1) * m_per_deg_lat
    length_m = math.hypot(dx, dy)
    if length_m == 0:
        raise ValueError("transect start and end are identical")

    width_m = width_mi * math.sqrt(SQ_M_PER_SQ_MI) / math.sqrt(1.0)
    width_m = width_mi * 1609.344

    # unit vector along the line, and its perpendicular
    ux, uy = dx / length_m, dy / length_m
    px, py = -uy, ux

    seg_len_m = (max_area_sq_mi * SQ_M_PER_SQ_MI * 0.85) / width_m
    n_seg = max(1, math.ceil(length_m / seg_len_m))
    step = length_m / n_seg

    def offset(along_m: float, across_m: float) -> Coord:
        ox = (ux * along_m + px * across_m) / m_per_deg_lon
        oy = (uy * along_m + py * across_m) / m_per_deg_lat
        return (lon1 + ox, lat1 + oy)

    out: List[List[Coord]] = []
    half = width_m / 2
    for k in range(n_seg):
        a0, a1 = k * step, (k + 1) * step
        ring = close_ring(
            [
                offset(a0, -half),
                offset(a1, -half),
                offset(a1, half),
                offset(a0, half),
            ]
        )
        out.append(ring)
    return out
