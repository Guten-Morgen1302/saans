"""Local runtime (no AWS account): DynamoDB Local + Telegram long polling + a
clock loop for the 18:00 / 06:30 / 07:00 IST jobs.

It reuses the same code paths as the Lambdas: every Telegram update goes
through webhook.handle (with an internal secret), and jobs go through
handlers.run_scheduled, so behaviour is identical to the AWS deployment.

    ┌──────── loop ────────┐
    │ getUpdates (25 s) ───┼──▶ webhook.handle ──▶ lifecycle / replay
    │ due_jobs(now) ───────┼──▶ handlers.run_scheduled(nightly|remind|expire)
    └──────────────────────┘      state file stops a restart re-running a job
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import date, datetime, time
from pathlib import Path

from saans.ingest import IST

log = logging.getLogger(__name__)

NIGHTLY, REMIND, EXPIRE = time(18), time(6, 30), time(7)


def due_jobs(now: datetime, state: dict[str, str]) -> list[str]:
    """Jobs that should run now and haven't run today (IST).

    nightly: any time from 18:00 (a laptop woken at 20:00 still sends the plan)
    remind:  06:30-06:59 only (a late reminder after expiry would be wrong)
    expire:  07:00-17:59 (expires a plan nobody answered, even if woken late)
    """
    t, today = now.astimezone(IST).time(), now.astimezone(IST).date().isoformat()
    windows = {"remind": (REMIND, EXPIRE), "expire": (EXPIRE, NIGHTLY), "nightly": (NIGHTLY, time.max)}
    return [job for job, (lo, hi) in windows.items() if lo <= t < hi and state.get(job) != today]


def load_state(path: Path) -> dict[str, str]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state), encoding="utf-8")


def update_event(update: dict, secret: str) -> dict:
    """Wrap a polled update exactly like the Lambda Function URL would deliver it."""
    return {"headers": {"x-telegram-bot-api-secret-token": secret}, "body": json.dumps(update)}


def ensure_table(dynamodb, name: str):
    existing = [t.name for t in dynamodb.tables.all()]
    if name not in existing:
        log.info("creating DynamoDB Local table %s", name)
        dynamodb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        ).wait_until_exists()
    return dynamodb.Table(name)


def new_internal_secret() -> str:
    return secrets.token_hex(32)
