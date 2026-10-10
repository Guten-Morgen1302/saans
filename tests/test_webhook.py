import base64
import json
from datetime import date

from saans import messages
from saans.lifecycle import send_plan
from saans.webhook import handle
from tests.conftest import SETTINGS, TEXTS

SECRET = "s3cret"
DAY = date(2025, 11, 11)
NOT_REGISTERED = messages.system("not_registered")


def event(update, secret=SECRET, b64=False):
    body = json.dumps(update)
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"headers": {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {},
            "body": body, "isBase64Encoded": b64}


def current_msg(deps, mode="live"):
    item = deps.store.get(SETTINGS.school_id, DAY, mode)
    return int(item["principal_msg_id"]) if item else 0


def tap(deps, user, data="approve|2025-11-11|live", message_id=None):
    if message_id is None:
        message_id = current_msg(deps, data.split("|")[-1])
    return {"callback_query": {"id": "cb1", "from": {"id": user}, "data": data,
                               "message": {"message_id": message_id}}}


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
    assert handle(event(tap(deps, 111), secret=None), deps, SECRET, run)["statusCode"] == 401
    assert handle(event(tap(deps, 111), secret="nope"), deps, SECRET, run)["statusCode"] == 401
    assert handle(event(tap(deps, 111)), deps, "", run)["statusCode"] == 401   # fails closed
    assert deps.messenger.sent == [] and calls == []


def test_bad_json_is_400(deps):
    e = {"headers": {"x-telegram-bot-api-secret-token": SECRET}, "body": "{not json"}
    assert handle(e, deps, SECRET, replays()[1])["statusCode"] == 400


def test_principal_approve_answers_first_then_posts(deps):
    send_plan(deps, DAY, "live", TEXTS)
    r = handle(event(tap(deps, 111)), deps, SECRET, replays()[1])
    assert r["statusCode"] == 200
    assert deps.messenger.to(SETTINGS.parent_channel_id) == [TEXTS["parents"]]
    ev = deps.messenger.events
    first_answer = next(i for i, e in enumerate(ev) if e[0] == "answer")
    first_post = next(i for i, e in enumerate(ev) if e == ("send", SETTINGS.parent_channel_id))
    assert first_answer < first_post
    assert deps.messenger.answers[0][1] == messages.toast("toast_sending")
    assert len(deps.messenger.answers) == 1          # a callback is answered exactly once


def test_second_tap_is_answered_without_reposting(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(deps, 111)), deps, SECRET, replays()[1])
    handle(event(tap(deps, 111)), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("toast_done")
    assert len(deps.messenger.to(SETTINGS.parent_channel_id)) == 1


def test_stranger_callback_is_rejected(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(deps, 555)), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("not_registered")
    assert deps.store.get(SETTINGS.school_id, DAY, "live")["status"] == "SENT"


def test_admin_is_not_principal_for_callbacks(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(deps, 999)), deps, SECRET, replays()[1])
    assert deps.store.get(SETTINGS.school_id, DAY, "live")["status"] == "SENT"


def test_tampered_callback_data_is_rejected(deps):
    handle(event(tap(deps, 111, data="approve|2025-11-11|prod")), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("toast_unknown")


def test_tap_from_a_replaced_replay_message_is_rejected(deps):
    send_plan(deps, DAY, "replay", TEXTS)
    old = current_msg(deps, "replay")
    send_plan(deps, DAY, "replay", TEXTS)
    handle(event(tap(deps, 111, data="approve|2025-11-11|replay", message_id=old)),
           deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("toast_replaced")
    assert deps.store.get(SETTINGS.school_id, DAY, "replay")["status"] == "SENT"


def test_tap_for_a_missing_plan(deps):
    handle(event(tap(deps, 111, message_id=5)), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("toast_gone")


def test_skip_via_webhook(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(deps, 111, data="skip|2025-11-11|live")), deps, SECRET, replays()[1])
    assert deps.messenger.answers[-1][1] == messages.toast("toast_skipped")
    assert deps.store.get(SETTINGS.school_id, DAY, "live")["status"] == "SKIPPED"


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
    assert deps.messenger.to(111) == [messages.system("help")]
    assert deps.messenger.to(555) == [NOT_REGISTERED]


def test_base64_body_is_decoded(deps):
    send_plan(deps, DAY, "live", TEXTS)
    handle(event(tap(deps, 111), b64=True), deps, SECRET, replays()[1])
    assert deps.messenger.to(SETTINGS.parent_channel_id) == [TEXTS["parents"]]


def test_unknown_update_types_are_ignored(deps):
    assert handle(event({"edited_message": {}}), deps, SECRET, replays()[1])["statusCode"] == 200
    assert deps.messenger.sent == []
