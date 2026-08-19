"""Tests for the conversion that can silently invalidate the whole project.

If these fail, nothing downstream is trustworthy.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from nightwatch.timeutil import (  # noqa: E402
    utc_hour_to_local, convert_tiles, sanity_check_peak_hours, CITY_TZ,
)

SUMMER = date(2025, 7, 22)
WINTER = date(2025, 1, 15)


class TestSummerOffsets:
    """July: Chicago is CDT (-5), New York EDT (-4), Phoenix MST (-7, no DST)."""

    def test_chicago_summer(self):
        p = utc_hour_to_local(20, SUMMER, CITY_TZ["chicago"])
        assert p.local_hour == 15
        assert p.utc_offset_hours == -5.0
        assert p.day_offset == 0

    def test_new_york_summer(self):
        p = utc_hour_to_local(20, SUMMER, CITY_TZ["new_york"])
        assert p.local_hour == 16
        assert p.utc_offset_hours == -4.0

    def test_phoenix_does_not_observe_dst(self):
        """The case that catches a naive 'subtract the winter offset' bug."""
        summer = utc_hour_to_local(20, SUMMER, CITY_TZ["phoenix"])
        winter = utc_hour_to_local(20, WINTER, CITY_TZ["phoenix"])
        assert summer.local_hour == 13
        assert winter.local_hour == 13
        assert summer.utc_offset_hours == winter.utc_offset_hours == -7.0

    def test_chicago_does_observe_dst(self):
        """Same UTC hour, different local hour by season. Phoenix must not."""
        summer = utc_hour_to_local(20, SUMMER, CITY_TZ["chicago"])
        winter = utc_hour_to_local(20, WINTER, CITY_TZ["chicago"])
        assert summer.local_hour == 15
        assert winter.local_hour == 14
        assert summer.utc_offset_hours != winter.utc_offset_hours


class TestDayBoundary:
    def test_early_utc_hour_rolls_back_a_day(self):
        """02:00 UTC is the previous evening in US zones."""
        p = utc_hour_to_local(2, SUMMER, CITY_TZ["chicago"])
        assert p.local_hour == 21
        assert p.day_offset == -1

    def test_midnight_utc(self):
        p = utc_hour_to_local(0, SUMMER, CITY_TZ["phoenix"])
        assert p.local_hour == 17
        assert p.day_offset == -1


class TestNightClassification:
    @pytest.mark.parametrize("utc_hour,expected", [
        (18, False),  # 13:00 Chicago -- afternoon
        (20, False),  # 15:00
        (24 - 24, True),   # 19:00 previous day
        (1, True),    # 20:00 previous day -- the late-peaking case
        (2, True),    # 21:00
    ])
    def test_is_night(self, utc_hour, expected):
        p = utc_hour_to_local(utc_hour, SUMMER, CITY_TZ["chicago"])
        assert p.is_night is expected, f"utc {utc_hour} -> local {p.local_hour}"


class TestValidation:
    @pytest.mark.parametrize("bad", [-1, 24, 25, 3.5, "12", None])
    def test_rejects_bad_hours(self, bad):
        with pytest.raises((ValueError, TypeError)):
            utc_hour_to_local(bad, SUMMER, CITY_TZ["chicago"])


class TestConvertTiles:
    def test_adds_local_fields(self):
        feats = [
            {"type": "Feature", "properties": {"tile_id": 0, "value": 20.0}},
            {"type": "Feature", "properties": {"tile_id": 1, "value": 18.0}},
        ]
        out = convert_tiles(feats, SUMMER, CITY_TZ["chicago"])
        assert out[0]["properties"]["local_hour"] == 15
        assert out[1]["properties"]["local_hour"] == 13
        # original untouched
        assert "local_hour" not in feats[0]["properties"]

    def test_handles_fractional_hours(self):
        """The docs show values like 6.03 -- take the integer hour."""
        feats = [{"type": "Feature", "properties": {"value": 6.03}}]
        out = convert_tiles(feats, SUMMER, CITY_TZ["chicago"])
        assert out[0]["properties"]["utc_hour"] == 6
        assert out[0]["properties"]["local_hour"] == 1

    def test_skips_features_without_value(self):
        feats = [{"type": "Feature", "properties": {"average_temperature": 31.2}}]
        out = convert_tiles(feats, SUMMER, CITY_TZ["chicago"])
        assert "local_hour" not in out[0]["properties"]


class TestSanityCheck:
    def test_passes_when_dense_peaks_later(self):
        ok, msg = sanity_check_peak_hours([12, 13, 12], [19, 20, 19])
        assert ok and "PASS" in msg

    def test_fails_when_inverted(self):
        """The signature of a timezone bug."""
        ok, msg = sanity_check_peak_hours([20, 19], [12, 13])
        assert not ok and "FAIL" in msg

    def test_warns_when_lag_is_negligible(self):
        ok, msg = sanity_check_peak_hours([14.0, 14.1], [14.2, 14.3])
        assert not ok and "WEAK" in msg

    def test_handles_empty(self):
        ok, _ = sanity_check_peak_hours([], [15])
        assert not ok
