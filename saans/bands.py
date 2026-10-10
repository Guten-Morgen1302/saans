"""Saans air bands: CPCB 24h PM2.5 breakpoints applied to hourly values.

This is a Saans convention, not the official AQI (the official AQI needs a
24h average and 3+ pollutants). Intervals are half-open on raw values, with
no rounding (eng review R10):

    Good x<=30 | Satisfactory 30<x<=60 | Moderate 60<x<=90 | Poor 90<x<=120
    Very Poor 120<x<=250 | Severe x>250
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum


class Band(IntEnum):
    GOOD = 0
    SATISFACTORY = 1
    MODERATE = 2
    POOR = 3
    VERY_POOR = 4
    SEVERE = 5

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def label_hi(self) -> str:
        return _INFO[self][0]

    @property
    def color(self) -> str:
        return _INFO[self][1]

    @property
    def dot(self) -> str:
        return _INFO[self][2]

    def name_in(self, lang: str) -> str:
        return self.label_hi if lang == "hi" else self.label


# One vocabulary for every message and the report (design 11A): CPCB Hindi
# band names and CPCB bulletin colours. Colours are fills only, never text.
_INFO = {
    Band.GOOD: ("अच्छा", "#00B050", "🟢"),
    Band.SATISFACTORY: ("संतोषजनक", "#92D050", "🟢"),
    Band.MODERATE: ("मध्यम", "#FFFF00", "🟡"),
    Band.POOR: ("खराब", "#FF9900", "🟠"),
    Band.VERY_POOR: ("बहुत खराब", "#FF0000", "🔴"),
    Band.SEVERE: ("गंभीर", "#C00000", "🟤"),
}

SAANS_BAND = {"en": "Saans air band", "hi": "साँस वायु श्रेणी"}

_UPPER_BOUNDS = [(30, Band.GOOD), (60, Band.SATISFACTORY), (90, Band.MODERATE),
                 (120, Band.POOR), (250, Band.VERY_POOR)]


def band(pm25: float) -> Band:
    for upper, b in _UPPER_BOUNDS:
        if pm25 <= upper:
            return b
    return Band.SEVERE


def rolling3(hourly: dict[datetime, float]) -> dict[datetime, float]:
    """Trailing 3-hour mean (hours h-2..h). Needs all three hours present."""
    out = {}
    for hour in hourly:
        window = [hourly.get(hour - timedelta(hours=k)) for k in range(3)]
        if all(v is not None for v in window):
            out[hour] = sum(window) / 3
    return out


def hourly_bands(hourly: dict[datetime, float]) -> dict[datetime, Band]:
    return {hour: band(v) for hour, v in rolling3(hourly).items()}
