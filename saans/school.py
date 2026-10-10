"""Demo school config: classes with asthma counts and the daily timetable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Activity:
    name: str          # "PE", "Assembly", "Maths", ...
    who: str           # class id, or "school" for school-wide events
    start: time
    end: time
    outdoor: bool

    @property
    def school_wide(self) -> bool:
        return self.who == "school"

    @property
    def is_pe(self) -> bool:
        return self.name == "PE"


@dataclass(frozen=True)
class School:
    id: str
    name: str
    asthma: dict[str, int]
    weekdays: frozenset[int]
    day: tuple[Activity, ...]

    def activities(self, weekday: int) -> tuple[Activity, ...]:
        return self.day if weekday in self.weekdays else ()

    def hour_window(self) -> range:
        """Forecast hours this timetable needs: first start - 2h .. last end (eng R7)."""
        first = min(a.start for a in self.day).hour
        last_end = max(a.end for a in self.day)
        last = last_end.hour - (1 if last_end.minute == 0 else 0)
        return range(max(first - 2, 0), last + 1)


def _t(s: str) -> time:
    return time.fromisoformat(s)


def load_school(path: Path) -> School:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    activities = []
    for a in raw["day"]:
        act = Activity(a["name"], str(a["who"]), _t(a["start"]), _t(a["end"]), bool(a["outdoor"]))
        if act.end <= act.start:
            raise ValueError(f"{act.name} for {act.who} ends before it starts")
        if not act.school_wide and act.who not in raw["classes"]:
            raise ValueError(f"{act.name} is for unknown class {act.who}")
        activities.append(act)
    return School(
        id=raw["id"],
        name=raw["name"],
        asthma={str(k): int(v) for k, v in raw["classes"].items()},
        weekdays=frozenset(raw["weekdays"]),
        day=tuple(sorted(activities, key=lambda a: (a.start, a.who))),
    )
