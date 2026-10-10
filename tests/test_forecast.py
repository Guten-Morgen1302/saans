from datetime import date, datetime, time, timedelta

import pytest

from saans.forecast import (
    SCHOOL_HOURS,
    Unavailable,
    actual_school_level,
    as_of,
    day_level,
    forecast,
    shape,
)
from saans.ingest import IST

# A clean synthetic "Delhi morning peak": hour factors around a daily level.
FACTORS = {6: 1.2, 7: 1.2, 8: 1.2, 9: 1.2, 10: 1.1, 11: 1.0, 12: 0.8, 13: 0.5}
SCHOOL_MEAN_FACTOR = sum(FACTORS[h] for h in SCHOOL_HOURS) / 6  # 0.9667


def make_series(levels: dict[date, float], hours=range(0, 24), stations=6):
    """levels = school-hours mean for each day; hourly values follow FACTORS."""
    series = {}
    for day, lvl in levels.items():
        base = lvl / SCHOOL_MEAN_FACTOR
        for hr in hours:
            series[datetime.combine(day, time(hr), IST)] = (base * FACTORS.get(hr, 1.0), stations)
    return series


def days(start: date, n: int, level=200.0):
    return {start + timedelta(days=k): level for k in range(n)}


D = date(2025, 11, 10)
T = datetime.combine(D, time(18), IST)


def test_as_of_hides_hours_after_cutoff_minus_lag():
    s = make_series({D: 200.0})
    visible = as_of(s, T)
    assert max(visible) == datetime.combine(D, time(16), IST)
    assert datetime.combine(D, time(17), IST) not in visible


def test_forecast_never_reads_the_future():
    base = days(D - timedelta(days=14), 15)
    s = make_series(base)
    f1 = forecast(s, T)
    # Poison everything after the cutoff: tomorrow's data and today's evening.
    poisoned = dict(s)
    for hr in range(17, 24):
        poisoned[datetime.combine(D, time(hr), IST)] = (9999.0, 6)
    for hr in range(24):
        poisoned[datetime.combine(D + timedelta(days=1), time(hr), IST)] = (9999.0, 6)
    f2 = forecast(poisoned, T)
    assert f2.level == f1.level and f2.hourly == f1.hourly


def test_full_day_level_is_plain_mean():
    s = make_series(days(D - timedelta(days=14), 15))
    shp = shape(s, D, range(6, 14), 2)
    level, used = day_level(s, D, shp, 2)
    assert level == pytest.approx(200.0) and used == 6


def test_morning_only_day_is_scaled_down_to_the_day_level():
    history = days(D - timedelta(days=14), 14, level=200.0)
    s = make_series(history)
    # Day D: only 08-10 present (the morning peak), like the real Nov 10 gap.
    s.update(make_series({D: 300.0}, hours=range(8, 11)))
    shp = shape(s, D, range(6, 14), 2)
    level, used = day_level(s, D, shp, 2)
    assert used == 3
    assert level == pytest.approx(300.0)  # not the inflated morning mean
    plain_morning = (300 / SCHOOL_MEAN_FACTOR) * (1.2 + 1.2 + 1.1) / 3
    assert plain_morning > 340  # what the old plain mean would have said


def test_fewer_than_three_school_hours_is_unavailable():
    s = make_series(days(D - timedelta(days=14), 14))
    s.update(make_series({D: 300.0}, hours=[8, 9]))
    shp = shape(s, D, range(6, 14), 2)
    with pytest.raises(Unavailable, match="2 of 6"):
        day_level(s, D, shp, 2)


def test_hours_with_one_station_do_not_count():
    s = make_series(days(D - timedelta(days=14), 14))
    s.update(make_series({D: 300.0}, hours=range(0, 24), stations=1))
    shp = shape(s, D - timedelta(days=1), range(6, 14), 2)
    with pytest.raises(Unavailable):
        day_level(s, D, shp, 2)


def test_shape_needs_ten_complete_days():
    s = make_series(days(D - timedelta(days=8), 9))
    with pytest.raises(Unavailable, match="complete days"):
        shape(s, D, range(6, 14), 2)


def test_rising_trend_is_added_and_clamped():
    levels = days(D - timedelta(days=14), 14, level=200.0)
    levels[D - timedelta(days=1)] = 100.0
    levels[D] = 300.0  # trend +200
    f = forecast(make_series(levels), T)
    assert f.level_today == pytest.approx(300.0)
    assert f.level == pytest.approx(400.0)  # 300 + 0.5*200, under the 1.5x cap
    levels[D - timedelta(days=1)] = 10.0  # trend +290 -> 445, cap 450 not hit
    assert forecast(make_series(levels), T).level == pytest.approx(445.0)
    levels[D - timedelta(days=1)] = 1000.0  # trend -700 -> -50, floor 0.7*300
    assert forecast(make_series(levels), T).level == pytest.approx(210.0)


def test_missing_yesterday_means_zero_trend():
    levels = days(D - timedelta(days=14), 15)
    s = make_series(levels)
    for hr in range(24):
        s.pop(datetime.combine(D - timedelta(days=1), time(hr), IST))
    f = forecast(s, T)
    assert f.level_yesterday is None and f.level == pytest.approx(f.level_today)


def test_persistence_ignores_trend():
    levels = days(D - timedelta(days=14), 14, level=200.0)
    levels[D] = 300.0
    f = forecast(make_series(levels), T, persistence=True)
    assert f.level == pytest.approx(300.0)


def test_hourly_forecast_follows_shape_for_timetable_window():
    s = make_series(days(D - timedelta(days=14), 15))
    f = forecast(s, T, hours=range(6, 15))  # a 14:30 period needs hour 14
    tomorrow = D + timedelta(days=1)
    assert f.target == tomorrow
    assert datetime.combine(tomorrow, time(14), IST) in f.hourly
    h8 = f.hourly[datetime.combine(tomorrow, time(8), IST)]
    h13 = f.hourly[datetime.combine(tomorrow, time(13), IST)]
    assert h8 / h13 == pytest.approx(1.2 / 0.5)


def test_naive_time_rejected():
    with pytest.raises(ValueError):
        forecast({}, datetime(2025, 11, 10, 18))


def test_actual_level_needs_five_school_hours():
    s = make_series({D: 200.0}, hours=range(8, 12))
    assert actual_school_level(s, D) is None
    s = make_series({D: 200.0}, hours=range(8, 13))
    assert actual_school_level(s, D) == pytest.approx(
        200 / SCHOOL_MEAN_FACTOR * sum(FACTORS[h] for h in range(8, 13)) / 5)
