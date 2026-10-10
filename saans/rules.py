"""Rules engine: tomorrow's hourly forecast + timetable -> actions[] (pure).

Precedence (eng review R2):
- An outdoor activity is at risk when its band reaches the LOWEST trigger among
  the rules that apply to it (so asthma classes are flagged first).
- The action comes from the MOST RESTRICTIVE rule whose trigger is met
  (cancel > indoor), so a Severe day always cancels, never just moves indoors.

An activity's band is the worst trailing-3h band over the hours it overlaps.
Rescheduling was dropped after go/no-go (b) failed; activities go indoors or
are cancelled.

    forecast f(h) ──▶ hourly_bands ──▶ per-activity band ──▶ applicable rules
                                                             │ at risk?   (lowest trigger)
                                                             ▼
                                                        action (most restrictive met)
         school-hours rules (windows, GRAP advisory) ◀── school-day bands
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

from saans.bands import Band, band, hourly_bands
from saans.forecast import SCHOOL_HOURS
from saans.ingest import IST
from saans.school import Activity, School

ACTION_RANK = {"indoor": 1, "cancel": 2}
ACTIVITY_SCOPES = {"asthma_pe", "all_pe", "school_events", "all_outdoor"}


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    trigger: Band
    applies_to: str
    action: str
    template: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Action:
    rule_id: str
    action: str
    template: str
    band: Band
    start: time
    end: time
    who: str = "school"
    activity: str | None = None
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    target: date
    school_hours_band: Band
    worst_band: Band
    worst_hour: time
    header: tuple[time, time, Band] | None   # "<date> · <band> HH:MM-HH:MM"
    actions: tuple[Action, ...]
    school_day: tuple[time, time] = (time(8), time(14))

    @property
    def no_action(self) -> bool:
        return not self.actions


def load_rules(path: Path) -> tuple[list[Rule], dict[str, str]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    urls = raw["sources"]
    rules = []
    for r in raw["rules"]:
        missing = [s for s in r["sources"] if s not in urls]
        if missing:
            raise ValueError(f"rule {r['id']} cites unknown sources {missing}")
        if r["kind"] == "adaptation" and len(r["sources"]) < 2:
            raise ValueError(f"adaptation rule {r['id']} must cite at least two sources")
        rules.append(Rule(r["id"], r["kind"], Band[r["trigger"]], r["applies_to"],
                          r["action"], r["template"], tuple(r["sources"])))
    return rules, urls


def _applies(rule: Rule, act: Activity, school: School) -> bool:
    if not act.outdoor:
        return False
    return {
        "asthma_pe": act.is_pe and school.asthma.get(act.who, 0) > 0,
        "all_pe": act.is_pe,
        "school_events": act.school_wide,
        "all_outdoor": True,
    }.get(rule.applies_to, False)


def _hours_overlapped(act: Activity) -> range:
    last = act.end.hour - (1 if act.end.minute == 0 else 0)
    return range(act.start.hour, last + 1)


def _activity_band(act: Activity, bands: dict[int, Band]) -> Band:
    hours = _hours_overlapped(act)
    missing = [h for h in hours if h not in bands]
    if missing:
        raise ValueError(f"forecast has no band for hours {missing} ({act.name} {act.who}); "
                         "derive the forecast window from the timetable")
    return max(bands[h] for h in hours)


def _span(hours: list[int], day: tuple[time, time]) -> tuple[time, time]:
    """First hour's start to last hour's end, clamped to the school day (design 16A)."""
    end = time(max(hours) + 1) if max(hours) < 23 else time(23, 59)
    return max(time(min(hours)), day[0]), min(end, day[1])


def protocol(forecast_hourly: dict[datetime, float], school: School, target: date,
             rules: list[Rule]) -> Plan:
    by_hour = {h.hour: b for h, b in hourly_bands(forecast_hourly).items() if h.date() == target}
    school_vals = [forecast_hourly[datetime.combine(target, time(h), IST)]
                   for h in SCHOOL_HOURS
                   if datetime.combine(target, time(h), IST) in forecast_hourly]
    if len(school_vals) < len(SCHOOL_HOURS):
        raise ValueError("forecast must cover school hours 08-13")
    school_band = band(sum(school_vals) / len(school_vals))
    school_bands = {h: by_hour[h] for h in SCHOOL_HOURS if h in by_hour}
    worst_hour = max(school_bands, key=lambda h: (school_bands[h], -h))
    worst_band = school_bands[worst_hour]
    todays = school.activities(target.weekday())
    day = ((min(a.start for a in todays), max(a.end for a in todays)) if todays
           else (time(SCHOOL_HOURS.start), time(SCHOOL_HOURS.stop)))

    actions: list[Action] = []
    for act in todays:
        applicable = [r for r in rules if r.applies_to in ACTIVITY_SCOPES and _applies(r, act, school)]
        if not applicable:
            continue
        b = _activity_band(act, by_hour)
        if b < min(r.trigger for r in applicable):
            continue
        met = [r for r in applicable if b >= r.trigger]
        chosen = max(met, key=lambda r: (ACTION_RANK[r.action], r.trigger))
        actions.append(Action(chosen.id, chosen.action, chosen.template, b, act.start, act.end,
                              act.who, act.name, chosen.sources))

    for rule in (r for r in rules if r.applies_to == "school_hours"):
        if rule.action == "windows_closed":
            hot = [h for h, b in school_bands.items() if b >= rule.trigger]
            if hot:
                start, end = _span(hot, day)
                actions.append(Action(rule.id, rule.action, rule.template,
                                      max(school_bands[h] for h in hot), start, end,
                                      sources=rule.sources))
        elif rule.action == "grap_advisory" and school_band >= rule.trigger:
            actions.append(Action(rule.id, rule.action, rule.template, school_band,
                                  day[0], day[1], sources=rule.sources))

    header = None
    top = [h for h, b in school_bands.items() if b == worst_band]
    if worst_band >= Band.POOR:
        start, end = _span(top, day)
        header = (start, end, worst_band)

    actions.sort(key=lambda a: (a.start, a.who, a.rule_id))
    return Plan(target, school_band, worst_band, time(worst_hour), header, tuple(actions), day)
