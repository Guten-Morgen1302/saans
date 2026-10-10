import base64
import json
from datetime import date

from saans.lifecycle import send_plan
from saans.webhook import HELP, NOT_REGISTERED, handle
from tests.conftest import SETTINGS, TEXTS

SECRET = "s3cret"
DAY = date(2025, 11, 11)


def event(update, secret=SECRET, b64=False):
    body = json.dumps(update)
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"headers": {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {},
            "body": body, "isBase64Encoded": b64}


def tap(user, data="approve|2025-11-11|live"):
    return {"callback_query": {"id": "cb1", "from": {"id": user}, "data": data}}


def msg(user, text, chat=None):
    return {"message": {"from": {"id": user}, "chat": {"id": chat or user}, "text": text}}


def replays():
    calls = []

    def run(day):
        calls.append(day)
        return "sent"
    return calls, run


def test_missing_or_wrong_secret_is_401(deps):
    calls, run = replays()
    assert handle(event(tap(111), secret=None), deps, SECRET, run)["statusCode"] == 401
    assert handle(event(tap(111), secret="nope"), deps, SECRET, run)["statusCode"] == 401
    assert handle(event(tap(111)), deps, "", run)["statusCode"] == 401   # unset secret fails closed
    assert deps.messenger.sent == [] and calls == []


def test_bad_json_is_400(deps):
    e = {"headers": {"x-telegram-bot-api-secret-token": SECRET}, "body": "{not json"}
    assert handle(e, deps, SECRET, replays()[1])["statusCode"] == 400


def test_principal_approve_posts(deps):
    send_plan(deps, DAY, "live", TEXTS)
    r = handle(event(tap(111)), deps, SECRET, replays()[1])
    assert r["statusCode"] == 200
    assert deps.messenger.to(SETTINGS.parent_channel_id) == [TEXTS["parents"]]
    assert deps.messenger.answers[-1][1].startswith("Approved")


def test_stranger_callback_is_rejected(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(555)), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == NOT_REGISTERED
    assert deps.store.get(SETTINGS.school_id, DAY, "live")["status"] == "SENT"


def test_admin_is_not_principal_for_callbacks(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(999)), deps, SECRET, replays()[1])
    assert deps.store.get(SETTINGS.school_id, DAY, "live")["status"] == "SENT"


def test_tampered_callback_data_is_rejected(deps):
    handle(event(tap(111, data="approve|2025-11-11|prod")), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == "Unknown button."


def test_admin_replay_runs(deps):
    calls, run = replays()
    handle(event(msg(999, "/replay 2025-11-10")), deps, SECRET, run)
    assert calls == [date(2025, 11, 10)]
    assert deps.messenger.to(999) == ["Replay 2025-11-10: sent"]


def test_principal_and_stranger_cannot_replay(deps):
    calls, run = replays()
    handle(event(msg(111, "/replay 2025-11-10")), deps, SECRET, run)
    handle(event(msg(555, "/replay 2025-11-10")), deps, SECRET, run)
    assert calls == []
    assert deps.messenger.to(555) == [NOT_REGISTERED]


def test_replay_needs_a_valid_date(deps):
    calls, run = replays()
    handle(event(msg(999, "/replay yesterday")), deps, SECRET, run)
    assert calls == [] and deps.messenger.to(999) == ["Usage: /replay YYYY-MM-DD"]


def test_other_messages(deps):
    handle(event(msg(111, "hello")), deps, SECRET, replays()[1])
    handle(event(msg(555, "hello")), deps, SECRET, replays()[1])
    assert deps.messenger.to(111) == [HELP]
    assert deps.messenger.to(555) == [NOT_REGISTERED]


def test_base64_body_is_decoded(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(111), b64=True), deps, SECRET, replays()[1])
    assert deps.messenger.to(SETTINGS.parent_channel_id) == [TEXTS["parents"]]


def test_unknown_update_types_are_ignored(deps):
    assert handle(event({"edited_message": {}}), deps, SECRET, replays()[1])["statusCode"] == 200
    assert deps.messenger.sent == []
