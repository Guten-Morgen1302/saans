from datetime import date, datetime
from pathlib import Path

import pytest
import responses

from saans import handlers
from saans.config import Config
from saans.ingest import BASE_URL, IST
from saans.lifecycle import send_plan
from tests.conftest import SETTINGS, TEXTS
from tests.test_pipeline import CTX


def runtime(deps, now):
    metrics = []
    cfg = Config(settings=SETTINGS, bot_token="b", webhook_secret="h", openaq_key="k",
                 table_name="saans", admin_chat_id=999)
    rt = handlers.Runtime(cfg=cfg, deps=deps, ctx=CTX, sensor_ids=[1, 2], metrics=metrics.append,
                          now=lambda: now)
    return rt, metrics


@responses.activate
def test_nightly_with_openaq_down_says_no_plan_and_still_beats(deps):
    for sid in (1, 2):
        responses.get(f"{BASE_URL}/sensors/{sid}/hours", status=503, body="down")
    rt, metrics = runtime(deps, datetime(2025, 11, 10, 18, 0, 5, tzinfo=IST))
    out = handlers.run_scheduled({"job": "nightly"}, rt)
    assert out["outcome"] == "unavailable"
    assert metrics == ["unavailable"]
    assert deps.messenger.to(999)[-1].startswith("18:00 run: unavailable")
    assert deps.messenger.to(999)[-1].endswith("no data received")
    assert "no plan tonight" in deps.messenger.to(SETTINGS.principal_chat_id)[0]


@responses.activate
def test_nightly_before_a_holiday_skips_but_still_beats(deps):
    rt, metrics = runtime(deps, datetime(2025, 10, 19, 18, 0, 5, tzinfo=IST))   # Diwali eve
    for sid in (1, 2):
        responses.get(f"{BASE_URL}/sensors/{sid}/hours", json={"results": []})
    out = handlers.run_scheduled({"job": "nightly"}, rt)
    assert out["outcome"] == "not-school-day" and metrics == ["not-school-day"]
    assert deps.messenger.to(SETTINGS.principal_chat_id) == []


def test_remind_and_expire_jobs(deps):
    today = date(2025, 11, 11)
    send_plan(deps, today, "live", TEXTS)
    rt, metrics = runtime(deps, datetime(2025, 11, 11, 6, 30, tzinfo=IST))
    assert handlers.run_scheduled({"job": "remind"}, rt)["outcome"] == "reminded"
    rt.now = lambda: datetime(2025, 11, 11, 7, 0, tzinfo=IST)
    assert handlers.run_scheduled({"job": "expire"}, rt)["outcome"] == "expired"
    assert metrics == []                  # only the nightly job feeds the heartbeat


def test_unknown_job_raises(deps):
    rt, _ = runtime(deps, datetime(2025, 11, 11, 6, 30, tzinfo=IST))
    with pytest.raises(ValueError):
        handlers.run_scheduled({"job": "nope"}, rt)
