from datetime import datetime

import pytest

from saans.bands import Band, band, hourly_bands, rolling3
from saans.ingest import IST


@pytest.mark.parametrize("pm25,expected", [
    (0, Band.GOOD), (30, Band.GOOD), (30.01, Band.SATISFACTORY),
    (60, Band.SATISFACTORY), (60.01, Band.MODERATE),
    (90, Band.MODERATE), (90.01, Band.POOR),
    (120, Band.POOR), (120.01, Band.VERY_POOR),
    (250, Band.VERY_POOR), (250.01, Band.SEVERE), (999, Band.SEVERE),
])
def test_half_open_edges(pm25, expected):
    assert band(pm25) is expected


def test_labels():
    assert Band.VERY_POOR.label == "Very Poor"


def h(hour):
    return datetime(2025, 11, 10, hour, tzinfo=IST)


def test_rolling3_is_trailing_and_needs_all_three_hours():
    hourly = {h(6): 90.0, h(7): 120.0, h(8): 150.0, h(10): 300.0}
    r = rolling3(hourly)
    assert r == {h(8): 120.0}  # 09 missing, so 10 has no full window


def test_hourly_bands_uses_rolling_mean_not_raw_value():
    hourly = {h(6): 100.0, h(7): 100.0, h(8): 400.0}  # raw 08:00 is Severe
    assert hourly_bands(hourly) == {h(8): Band.VERY_POOR}  # mean 200
