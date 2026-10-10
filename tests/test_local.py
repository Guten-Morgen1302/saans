import json
from datetime import datetime

import boto3
import pytest
import responses
from moto import mock_aws

from saans import local, summary
from saans.ingest import IST
from saans.telegram import TelegramMessenger

TODAY = "2026-10-05"


def at(h, m=0):
    return datetime(2026, 10, 5, h, m, tzinfo=IST)


@pytest.mark.parametrize("now,expected", [
    (at(6, 29), []),
    (at(6, 30), ["remind"]),
    (at(6, 59), ["remind"]),
    (at(7, 0), ["expire"]),
    (at(9, 15), ["expire"]),          # laptop woken late still expires the plan
    (at(17, 59), ["expire"]),
    (at(18, 0), ["nightly"]),
    (at(21, 40), ["nightly"]),        # laptop woken late still sends tonight's plan
])
def test_due_jobs_windows(now, expected):
    assert local.due_jobs(now, {}) == expected


def test_jobs_run_once_per_day():
    assert local.due_jobs(at(18, 5), {"nightly": TODAY}) == []
    assert local.due_jobs(at(18, 5), {"nightly": "2026-10-04"}) == ["nightly"]


def test_state_round_trip(tmp_path):
    p = tmp_path / "s.json"
    assert local.load_state(p) == {}
    local.save_state(p, {"nightly": TODAY})
    assert local.load_state(p) == {"nightly": TODAY}
    p.write_text("{broken")
    assert local.load_state(p) == {}


def test_update_event_matches_function_url_shape():
    e = local.update_event({"update_id": 7}, "sec")
    assert e["headers"]["x-telegram-bot-api-secret-token"] == "sec"
    assert json.loads(e["body"]) == {"update_id": 7}


def test_ensure_table_creates_once(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    with mock_aws():
        ddb = boto3.resource("dynamodb")
        t1 = local.ensure_table(ddb, "saans")
        t2 = local.ensure_table(ddb, "saans")
        assert t1.name == t2.name == "saans"
        assert [t.name for t in ddb.tables.all()] == ["saans"]


@responses.activate
def test_get_updates_and_delete_webhook():
    base = "https://api.telegram.org/botT"
    responses.post(f"{base}/getUpdates", json={"ok": True, "result": [{"update_id": 3}]})
    responses.post(f"{base}/deleteWebhook", json={"ok": True, "result": True})
    tg = TelegramMessenger("T")
    assert tg.get_updates(offset=3, timeout=1) == [{"update_id": 3}]
    assert json.loads(responses.calls[0].request.body)["offset"] == 3
    tg.delete_webhook()


def test_model_provider_selection(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SAANS_BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("SAANS_SUMMARY_PROVIDER", raising=False)
    assert summary.model_call_from_env() is None                 # nothing configured
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    assert callable(summary.model_call_from_env())               # built, no network call
    monkeypatch.setenv("SAANS_SUMMARY_PROVIDER", "none")
    assert summary.model_call_from_env() is None
