"""School-day calendar shared by the nightly gate and the backtest (eng review R11)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SchoolCalendar:
    school_weekdays: frozenset[int]
    holidays: dict[date, str]

    def is_school_day(self, day: date) -> bool:
        return day.weekday() in self.school_weekdays and day not in self.holidays


def load_calendar(path: Path) -> SchoolCalendar:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    holidays = {
        (d if isinstance(d, date) else date.fromisoformat(d)): name
        for d, name in (raw.get("holidays") or {}).items()
    }
    return SchoolCalendar(frozenset(raw["school_weekdays"]), holidays)
