"""One evening run: data → forecast → rules → messages → principal.

Shared by the 18:00 nightly job (live OpenAQ data) and /replay (packaged
2025 data). The forecast method is pure persistence (eng R6 selection, T3).

    series ──forecast(as_of 18:00)──▶ hourly f(h) ──protocol──▶ Plan
      │ Unavailable → "no plan tonight" message        │ no_action → one-line note
      │                                                 └▶ summary (validated) → render → send_plan
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from saans import messages
from saans.calendar import SchoolCalendar
from saans.forecast import SHAPE_DAYS, Series, Unavailable, forecast
from saans.ingest import IST, OpenAQClient, fetch_series, station_mean
from saans.lifecycle import Deps, send_plan
from saans.rules import Rule, protocol
from saans.school import School
from saans.summary import ModelCall, summarize

log = logging.getLogger(__name__)

REPLAY_CSV = Path(__file__).parent / "data" / "replay_2025q4.csv"


@dataclass(frozen=True)
class Context:
    school: School
    calendar: SchoolCalendar
    rules: list[Rule]
    model_call: ModelCall | None = None


def load_series_csv(path: Path = REPLAY_CSV) -> Series:
    with Path(path).open(encoding="utf-8") as f:
        return {datetime.fromisoformat(r["hour_ist"]): (float(r["pm25_mean"]), int(r["stations"]))
                for r in csv.DictReader(f)}


@dataclass(frozen=True)
class Backup:
    """Low-cost sensors used when the CPCB feed is stale (config/stations.yaml)."""
    sensors: dict[int, float]              # sensor id -> divide_by correction
    stale_after: timedelta = timedelta(hours=6)
    min_stations: int = 1


@dataclass(frozen=True)
class LiveData:
    series: Series
    source: str                            # "cpcb" | "backup" | "none"
    latest: datetime | None
    min_stations: int = 2


def live_series(client: OpenAQClient, sensor_ids: list[int], made_at: datetime) -> Series:
    """Enough history for the forecast: 14 shape days plus yesterday and today."""
    start = datetime.combine(made_at.date() - timedelta(days=SHAPE_DAYS + 1), time(0), IST)
    return station_mean(fetch_series(client, sensor_ids, start, made_at))


def live_data(client: OpenAQClient, sensor_ids: list[int], made_at: datetime,
              backup: Backup | None = None) -> LiveData:
    """CPCB first; the backup sensors only if CPCB's newest hour is too old."""
    cpcb = live_series(client, sensor_ids, made_at)
    latest = max(cpcb) if cpcb else None
    if latest and made_at - latest <= (backup.stale_after if backup else timedelta(days=365)):
        return LiveData(cpcb, "cpcb", latest)
    if not backup:
        return LiveData(cpcb, "cpcb" if cpcb else "none", latest)
    log.warning("CPCB feed stale (newest %s); using backup sensors", latest)
    start = datetime.combine(made_at.date() - timedelta(days=SHAPE_DAYS + 1), time(0), IST)
    raw = fetch_series(client, list(backup.sensors), start, made_at)
    for sid, s in raw.items():
        s.values = {h: v / backup.sensors[sid] for h, v in s.values.items()}
    series = station_mean(raw)
    if not series:
        return LiveData(cpcb, "none", latest)
    return LiveData(series, "backup", max(series), backup.min_stations)


def run_evening(deps: Deps, ctx: Context, series: Series, made_at: datetime, mode: str,
                backup_data: bool = False, min_stations: int = 2) -> str:
    """Returns the outcome used by the heartbeat: sent / already_sent / no-action /
    unavailable / not-school-day."""
    target = made_at.astimezone(IST).date() + timedelta(days=1)
    principal = deps.settings.principal_chat_id
    if mode == "live" and not ctx.calendar.is_school_day(target):
        return "not-school-day"
    try:
        f = forecast(series, made_at, hours=ctx.school.hour_window(), persistence=True,
                     min_stations=min_stations)
    except Unavailable as e:
        log.warning("forecast unavailable for %s (%s): %s", target, mode, e)
        deps.messenger.send(principal, messages.render_unavailable(target))
        return "unavailable"

    plan = protocol(f.hourly, ctx.school, target, ctx.rules)
    if plan.no_action:
        deps.messenger.send(principal, messages.render_no_action(plan))
        return "no-action"

    replay_of = made_at.date() if mode == "replay" else None
    texts = messages.render_plan(plan, ctx.school, replay_of=replay_of,
                                 summary=summarize(plan, ctx.school, ctx.model_call),
                                 backup_data=backup_data)
    return send_plan(deps, target, mode, texts)


def run_replay(deps: Deps, ctx: Context, day: date, series: Series | None = None) -> str:
    """/replay <day>: rerun the evening of `day` on packaged data, demo channels only."""
    made_at = datetime.combine(day, time(18), IST)
    return run_evening(deps, ctx, series if series is not None else load_series_csv(), made_at,
                       "replay")


def evening_made_at(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(IST).date(), time(18), IST)


def timed(fn: Callable[[], str]) -> tuple[str, float]:
    start = datetime.now(IST)
    outcome = fn()
    return outcome, (datetime.now(IST) - start).total_seconds()
