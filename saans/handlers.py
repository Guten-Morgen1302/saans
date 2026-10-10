"""AWS Lambda entry points (eng D5): `webhook` (Function URL) and `scheduled`
(EventBridge crons: 18:00 nightly, 06:30 remind, 07:00 expire, all IST).

Heartbeat (CEO D10 / eng R15): every nightly run emits NightlyRunCompleted=1
(no dimensions, watched by the alarm; plus an `outcome` dimension for charts)
and sends the admin a one-line summary, including runs that skip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from saans import lifecycle, pipeline, webhook
from saans.calendar import load_calendar
from saans.config import Config, load_config
from saans.ingest import IST, OpenAQClient, OpenAQError
from saans.lifecycle import Deps
from saans.rules import load_rules
from saans.school import load_school
from saans.summary import model_call_from_env

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
_runtime: "Runtime | None" = None


@dataclass
class Runtime:
    cfg: Config
    deps: Deps
    ctx: pipeline.Context
    sensor_ids: list[int]
    metrics: Callable[[str], None]
    now: Callable[[], datetime] = lambda: datetime.now(IST)
    backup: pipeline.Backup | None = None
    trial: object | None = None


def load_stations(path: Path = ROOT / "config" / "stations.yaml") -> tuple[list[int], pipeline.Backup | None]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ids = [int(s["sensor_id"]) for s in raw["stations"]]
    fb = raw.get("fallback")
    if not fb:
        return ids, None
    from datetime import timedelta
    return ids, pipeline.Backup(
        sensors={int(s["sensor_id"]): float(s["divide_by"]) for s in fb["sensors"]},
        stale_after=timedelta(hours=float(fb.get("stale_after_hours", 6))),
        min_stations=int(fb.get("min_stations", 1)),
    )


def _build_runtime() -> Runtime:
    import boto3

    from saans.store import PlanStore
    from saans.telegram import TelegramMessenger

    school = load_school(ROOT / "school.yaml")
    cfg = load_config(school.id)
    ctx = pipeline.Context(
        school=school,
        calendar=load_calendar(ROOT / "backtest" / "calendar_2025.yaml",
                               ROOT / "config" / "calendar_2026.yaml"),
        rules=load_rules(ROOT / "rules.yaml")[0],
        model_call=model_call_from_env(),
    )
    sensor_ids, backup = load_stations()
    deps = Deps(store=PlanStore(boto3.resource("dynamodb").Table(cfg.table_name)),
                messenger=TelegramMessenger(cfg.bot_token), settings=cfg.settings)
    cloudwatch = boto3.client("cloudwatch")

    def metrics(outcome: str) -> None:
        cloudwatch.put_metric_data(Namespace="Saans", MetricData=[
            {"MetricName": "NightlyRunCompleted", "Value": 1, "Unit": "Count"},
            {"MetricName": "NightlyRunCompleted", "Value": 1, "Unit": "Count",
             "Dimensions": [{"Name": "outcome", "Value": outcome}]},
        ])

    from saans.trial import Trial
    return Runtime(cfg, deps, ctx, sensor_ids, metrics, backup=backup, trial=Trial(deps, ctx))


def _rt() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = _build_runtime()      # cold start: secrets from SSM read once
    return _runtime


def webhook_handler(event, context):
    rt = _rt()
    return webhook.handle(event, rt.deps, rt.cfg.webhook_secret,
                          lambda day: pipeline.run_replay(rt.deps, rt.ctx, day), trial=rt.trial)


def scheduled_handler(event, context):
    return run_scheduled(event, _rt())


def run_scheduled(event: dict, rt: Runtime) -> dict:
    job = (event or {}).get("job")
    now = rt.now()
    today = now.astimezone(IST).date()
    if job == "remind":
        return {"job": job, "outcome": lifecycle.remind(rt.deps, today)}
    if job == "expire":
        return {"job": job, "outcome": lifecycle.expire(rt.deps, today)}
    if job != "nightly":
        raise ValueError(f"unknown job {job!r}")

    made_at = pipeline.evening_made_at(now)
    start = datetime.now(IST)
    try:
        data = pipeline.live_data(OpenAQClient(rt.cfg.openaq_key), rt.sensor_ids, made_at, rt.backup)
    except OpenAQError as e:
        log.error("openaq fetch failed: %s", e)
        data = pipeline.LiveData({}, "none", None)   # → Unavailable → "no plan tonight"
    outcome = pipeline.run_evening(rt.deps, rt.ctx, data.series, made_at, "live",
                                   backup_data=data.source == "backup",
                                   min_stations=data.min_stations)
    secs = (datetime.now(IST) - start).total_seconds()
    rt.metrics(outcome)
    latest = (f"{data.source} data to {data.latest:%d %b %H:%M}" if data.latest
              else "no data received")
    try:
        rt.deps.messenger.send(rt.cfg.admin_chat_id, f"18:00 run: {outcome}, {secs:.1f}s, {latest}")
    except lifecycle.MessengerError as e:
        log.error("heartbeat message failed: %s", e)
    log.info("nightly run for %s: %s in %.1fs", made_at.date(), outcome, secs)
    return {"job": job, "outcome": outcome, "seconds": round(secs, 1)}
