"""UTC -> local hour conversion.

READ THIS BEFORE CHANGING ANYTHING IN THIS FILE.

`analytic_type: "time_of_measure"` returns the hour at which each tile peaked
as an integer 0-23 **in UTC**. NightWatch's entire thesis is a claim about
*local* clock time -- "this block peaks at 19:40 and is still radiating at
2am". If this conversion is wrong, the map still renders beautifully and the
conclusion is entirely false. That is the most dangerous failure mode in the
project, so the conversion happens exactly once, here, at ingest, and it is
unit-tested.

Two traps this module exists to avoid:

1.  A US transect spans four time zones and most of them observe daylight
    saving. You cannot subtract a constant. Phoenix in particular sits in
    America/Phoenix and does *not* observe DST, so in summer it is UTC-7
    while Denver -- nominally the same zone -- is UTC-6.

2.  Converting an hour-of-day requires a date, because the offset depends on
    it. Never convert a bare hour.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime, time, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    raise SystemExit(
        "NightWatch needs Python 3.9+ for zoneinfo. On 3.8, "
        "`pip install backports.zoneinfo` and adjust this import."
    )


def _zone(tz_name: str) -> "ZoneInfo":
    """ZoneInfo with an error message that names the actual fix.

    Windows ships no system timezone database, so a bare ZoneInfo lookup
    fails there with a message that does not mention the cause. The `tzdata`
    package supplies it. This is in requirements.txt, but the check stays
    here because the raw error is genuinely misleading.
    """
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ZoneInfoNotFoundError(
            f"No timezone data for {tz_name!r}. On Windows (and slim Linux "
            f"images) there is no system tz database -- run "
            f"`pip install tzdata` inside your venv. Original: {exc}"
        ) from exc


# Timezones for the candidate demo cities. Note Phoenix is deliberately its
# own zone -- this is the case that catches a naive implementation.
CITY_TZ = {
    "chicago": "America/Chicago",
    "new_york": "America/New_York",
    "phoenix": "America/Phoenix",
    "houston": "America/Chicago",
    "miami": "America/New_York",
    "los_angeles": "America/Los_Angeles",
}


@dataclass(frozen=True)
class LocalPeak:
    """The result of converting one tile's peak hour."""

    utc_hour: int
    local_hour: int
    local_datetime: datetime
    day_offset: int  # -1, 0 or +1 relative to the queried date
    tz_name: str
    utc_offset_hours: float

    @property
    def is_night(self) -> bool:
        """Peaks at or after 19:00, or before 06:00, local."""
        return self.local_hour >= 19 or self.local_hour < 6


def utc_hour_to_local(
    utc_hour: int,
    on_date: Date,
    tz_name: str,
) -> LocalPeak:
    """Convert a `time_of_measure` hour (0-23 UTC) to local clock time.

    `on_date` is the `start_date` you sent to /v1/heatmap, interpreted as the
    UTC date the analytic covers.
    """
    if not isinstance(utc_hour, int) or not 0 <= utc_hour <= 23:
        raise ValueError(f"utc_hour must be an int 0-23, got {utc_hour!r}")

    tz = _zone(tz_name)
    aware_utc = datetime.combine(on_date, time(hour=utc_hour), tzinfo=timezone.utc)
    local = aware_utc.astimezone(tz)

    offset = local.utcoffset() or timedelta(0)
    return LocalPeak(
        utc_hour=utc_hour,
        local_hour=local.hour,
        local_datetime=local,
        day_offset=(local.date() - on_date).days,
        tz_name=tz_name,
        utc_offset_hours=offset.total_seconds() / 3600.0,
    )


def convert_tiles(
    features: list,
    on_date: Date,
    tz_name: str,
    value_key: str = "value",
) -> list:
    """Add local-hour fields to every feature of a time_of_measure response.

    Mutates nothing; returns new feature dicts with `local_hour`,
    `utc_hour`, `day_offset` and `is_night` added to `properties`.

    Note the schema difference the API docs bury: time_of_measure /
    exceedance / persistence put their number in `properties.value`, whereas
    tcm uses `average_temperature` / `min_temperature` / `max_temperature`.
    Code written against one finds nothing in the other.
    """
    out = []
    for feat in features:
        props = dict(feat.get("properties", {}))
        raw = props.get(value_key)
        if raw is None:
            out.append(feat)
            continue

        # time_of_measure returns a float in some responses (e.g. 6.03);
        # the integer part is the hour.
        utc_hour = int(float(raw)) % 24
        peak = utc_hour_to_local(utc_hour, on_date, tz_name)

        props.update(
            utc_hour=peak.utc_hour,
            local_hour=peak.local_hour,
            day_offset=peak.day_offset,
            is_night=peak.is_night,
            tz_name=peak.tz_name,
        )
        new = dict(feat)
        new["properties"] = props
        out.append(new)
    return out


# --------------------------------------------------------------------------
# the sanity check that catches a timezone bug before it reaches the map
# --------------------------------------------------------------------------
def sanity_check_peak_hours(
    open_land_hours: list, dense_urban_hours: list
) -> tuple:
    """Park tiles must peak EARLIER than dense urban tiles.

    This is physics, not a hypothesis: vegetated and open land sheds heat by
    evapotranspiration and has low thermal mass, so it peaks near solar noon.
    Masonry and asphalt store energy and peak hours later. If your data says
    otherwise, you have a timezone bug -- not a discovery. Stop and fix it
    before building anything downstream.

    Returns (passed, message).
    """
    if not open_land_hours or not dense_urban_hours:
        return False, "not enough tiles in one or both groups to check"

    open_mean = sum(open_land_hours) / len(open_land_hours)
    dense_mean = sum(dense_urban_hours) / len(dense_urban_hours)
    lag = dense_mean - open_mean

    if lag <= 0:
        return False, (
            f"FAIL: open land peaks at {open_mean:.1f}h, dense urban at "
            f"{dense_mean:.1f}h. Dense areas should peak LATER. This is "
            f"almost certainly a UTC->local bug. Do not proceed."
        )
    if lag < 0.5:
        return False, (
            f"WEAK: only {lag:.2f}h of lag between open land and dense "
            f"urban. Either the transect lacks morphological contrast or "
            f"the conversion is off. Investigate before harvesting more."
        )
    return True, (
        f"PASS: open land peaks {open_mean:.1f}h, dense urban {dense_mean:.1f}h "
        f"-- {lag:.2f}h of thermal lag."
    )
