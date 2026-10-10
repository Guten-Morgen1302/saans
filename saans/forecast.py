"""Evening-before PM2.5 forecast (stdlib only; shared by Lambda and backtest).

Made at time t (18:00 IST on day D) for day D+1, using only hours that were
available at t - lag (design doc: "Availability lag"; default lag 2h, so at
18:00 the last usable hour is 16:00).

    L_D   = school-hours level of day D (curve-adjusted when hours are missing)
    T     = L_D - L_{D-1}   (0 when L_{D-1} is unavailable)
    L_hat = clamp(L_D + 0.5*T, 0.7*L_D, 1.5*L_D)
    s(h)  = median over complete days in D-13..D of PM(day, h) / L_day
    f(h)  = L_hat * s(h)

Pure persistence (L_hat = L_D) is the comparison baseline (eng review R6).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from saans.ingest import IST

SCHOOL_HOURS = range(8, 14)          # 08:00-13:59, the ground-truth window
DEFAULT_HOURS = range(6, 14)         # forecast window when no timetable is given
LAG = timedelta(hours=2)
MIN_LEVEL_HOURS = 3                  # of 6 school hours (user decision 2026-09-27)
MIN_SHAPE_DAYS = 10                  # of 14
SHAPE_DAYS = 14

# Hourly station-mean series: IST hour -> (mean PM2.5, stations reporting)
Series = dict[datetime, tuple[float, int]]


class Unavailable(Exception):
    """Not enough data to forecast honestly; the message says why."""


@dataclass
class Forecast:
    made_at: datetime
    target: date
    level_today: float
    level_yesterday: float | None
    level: float
    shape: dict[int, float]
    hourly: dict[datetime, float] = field(default_factory=dict)
    today_hours_used: int = 6


def as_of(series: Series, t: datetime, lag: timedelta = LAG) -> Series:
    """Only the hours that had been published by time t."""
    cutoff = t - lag
    return {h: v for h, v in series.items() if h <= cutoff}


def _hour(day: date, h: int) -> datetime:
    return datetime.combine(day, time(h), IST)


def _day_values(series: Series, day: date, hours, min_stations: int) -> dict[int, float]:
    out = {}
    for h in hours:
        entry = series.get(_hour(day, h))
        if entry and entry[1] >= min_stations:
            out[h] = entry[0]
    return out


def shape(series: Series, day: date, hours, min_stations: int) -> dict[int, float]:
    """Median ratio PM(h)/L_day over complete days D-13..D."""
    ratios: dict[int, list[float]] = {h: [] for h in hours}
    usable = 0
    for k in range(SHAPE_DAYS):
        d = day - timedelta(days=k)
        school = _day_values(series, d, SCHOOL_HOURS, min_stations)
        window = _day_values(series, d, hours, min_stations)
        if len(school) < len(SCHOOL_HOURS) or len(window) < len(hours):
            continue
        level = sum(school.values()) / len(school)
        if level <= 0:
            continue
        usable += 1
        for h in hours:
            ratios[h].append(window[h] / level)
    if usable < MIN_SHAPE_DAYS:
        raise Unavailable(f"only {usable} complete days in the 14 before {day} (need {MIN_SHAPE_DAYS})")
    return {h: statistics.median(r) for h, r in ratios.items()}


def day_level(series: Series, day: date, shp: dict[int, float], min_stations: int) -> tuple[float, int]:
    """School-hours level; curve-adjusted from >=3 hours when some are missing."""
    vals = _day_values(series, day, SCHOOL_HOURS, min_stations)
    if len(vals) < MIN_LEVEL_HOURS:
        raise Unavailable(f"{day}: {len(vals)} of 6 school hours available (need {MIN_LEVEL_HOURS})")
    plain = sum(vals.values()) / len(vals)
    if len(vals) == len(SCHOOL_HOURS):
        return plain, len(vals)
    avail_shape = sum(shp[h] for h in vals) / len(vals)
    full_shape = sum(shp[h] for h in SCHOOL_HOURS) / len(SCHOOL_HOURS)
    return plain / avail_shape * full_shape, len(vals)


def forecast(
    series: Series,
    t: datetime,
    hours=DEFAULT_HOURS,
    min_stations: int = 2,
    persistence: bool = False,
) -> Forecast:
    """Forecast day t.date()+1 using only data available at t. Raises Unavailable."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    hours = sorted(set(hours) | set(SCHOOL_HOURS))
    visible = as_of(series, t)
    today = t.astimezone(IST).date()
    shp = shape(visible, today, hours, min_stations)
    level_today, used = day_level(visible, today, shp, min_stations)
    try:
        level_yesterday, _ = day_level(visible, today - timedelta(days=1), shp, min_stations)
    except Unavailable:
        level_yesterday = None

    if persistence:
        level = level_today
    else:
        trend = level_today - level_yesterday if level_yesterday is not None else 0.0
        level = min(max(level_today + 0.5 * trend, 0.7 * level_today), 1.5 * level_today)

    target = today + timedelta(days=1)
    return Forecast(
        made_at=t,
        target=target,
        level_today=level_today,
        level_yesterday=level_yesterday,
        level=level,
        shape=shp,
        hourly={_hour(target, h): level * shp[h] for h in hours},
        today_hours_used=used,
    )


def actual_school_level(series: Series, day: date, min_stations: int = 2) -> float | None:
    """Ground truth: plain mean over school hours, or None if fewer than 5 of 6."""
    vals = _day_values(series, day, SCHOOL_HOURS, min_stations)
    if len(vals) < 5:
        return None
    return sum(vals.values()) / len(vals)
