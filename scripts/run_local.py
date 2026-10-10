"""Run Saans on this laptop, with no AWS account.

Needs: Docker Desktop running (`docker compose up -d` starts DynamoDB Local)
and .env filled in (see .env.example). Leave this running; the 18:00 plan is
sent only while it runs.

Usage: python scripts/run_local.py
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.env import load_env  # noqa: E402

load_env()
os.environ["SAANS_LOCAL"] = "1"                               # secrets from .env, not SSM
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "local-" + os.urandom(16).hex())
for k, v in {"AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local",
             "AWS_DEFAULT_REGION": "ap-south-1"}.items():
    os.environ.setdefault(k, v)                               # DynamoDB Local ignores these

import boto3  # noqa: E402
import yaml  # noqa: E402

from saans import handlers, pipeline, webhook  # noqa: E402
from saans.calendar import load_calendar  # noqa: E402
from saans.config import load_config  # noqa: E402
from saans.ingest import IST  # noqa: E402
from saans.lifecycle import Deps, MessengerError  # noqa: E402
from saans.local import due_jobs, ensure_table, load_state, save_state, update_event  # noqa: E402
from saans.rules import load_rules  # noqa: E402
from saans.school import load_school  # noqa: E402
from saans.store import PlanStore  # noqa: E402
from saans.summary import model_call_from_env  # noqa: E402
from saans.telegram import TelegramMessenger  # noqa: E402

STATE = ROOT / "data" / "local_state.json"
log = logging.getLogger("saans.local")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    school = load_school(ROOT / "school.yaml")
    cfg = load_config(school.id)
    endpoint = os.environ.get("SAANS_DYNAMODB_ENDPOINT", "http://localhost:8000")
    table = ensure_table(boto3.resource("dynamodb", endpoint_url=endpoint), cfg.table_name)
    tg = TelegramMessenger(cfg.bot_token)
    tg.delete_webhook()

    model_call = model_call_from_env()
    ctx = pipeline.Context(
        school=school,
        calendar=load_calendar(ROOT / "backtest" / "calendar_2025.yaml",
                               ROOT / "config" / "calendar_2026.yaml"),
        rules=load_rules(ROOT / "rules.yaml")[0],
        model_call=model_call,
    )
    deps = Deps(store=PlanStore(table), messenger=tg, settings=cfg.settings)
    stations = yaml.safe_load((ROOT / "config" / "stations.yaml").read_text(encoding="utf-8"))
    rt = handlers.Runtime(cfg, deps, ctx, [int(s["sensor_id"]) for s in stations["stations"]],
                          metrics=lambda outcome: log.info("heartbeat: nightly %s", outcome))
    log.info("Saans running locally (DynamoDB %s, summary %s). Ctrl+C to stop.",
             endpoint, "on" if model_call else "off")

    offset, state = None, load_state(STATE)
    while True:
        for job in due_jobs(datetime.now(IST), state):
            try:
                log.info("job %s -> %s", job, handlers.run_scheduled({"job": job}, rt))
            except Exception:  # noqa: BLE001 - keep the bot alive; the admin line shows gaps
                log.exception("job %s failed", job)
            state[job] = datetime.now(IST).date().isoformat()
            save_state(STATE, state)
        try:
            updates = tg.get_updates(offset)
        except MessengerError as e:
            log.warning("polling failed (%s); retrying in 10s", e)
            time.sleep(10)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                webhook.handle(update_event(update, cfg.webhook_secret), deps, cfg.webhook_secret,
                               lambda day: pipeline.run_replay(deps, ctx, day))
            except Exception:  # noqa: BLE001
                log.exception("update %s failed", update.get("update_id"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
