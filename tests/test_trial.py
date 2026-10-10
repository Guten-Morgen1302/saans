import json
from datetime import datetime

import pytest

from saans.ingest import IST
from saans.trial import LIMIT_REACHED, PUBLIC_HELP, Trial
from saans.webhook import handle
from tests.conftest import SETTINGS
from tests.test_pipeline import CTX, SERIES

SECRET = "s"
STRANGER = 4242


@pytest.fixture
def trial(deps):
    return Trial(deps, CTX, series=SERIES, now=lambda: datetime(2026, 10, 6, 12, tzinfo=IST))


def ev(update):
    return {"headers": {"x-telegram-bot-api-secret-token": SECRET}, "body": json.dumps(update)}


def msg(text, user=STRANGER, chat_type="private"):
    return {"message": {"from": {"id": user}, "chat": {"id": user, "type": chat_type}, "text": text}}


def tap(data, user=STRANGER, message_id=77, text="PLAN"):
    return {"callback_query": {"id": "cb", "from": {"id": user}, "data": data,
                               "message": {"message_id": message_id, "chat": {"id": user}, "text": text}}}


def run(deps, trial, update):
    return handle(ev(update), deps, SECRET, lambda d: "sent", trial=trial)


def test_stranger_try_gets_the_replay_plan_with_demo_buttons(deps, trial):
    run(deps, trial, msg("/try"))
    intro, plan = deps.messenger.to(STRANGER)
    assert "Saans demo" in intro
    assert "Tue 11 Nov · 🟤 Severe" in plan
    assert deps.messenger.sent[-1][2] == [("Approve & send (demo)", "try|approve"),
                                          ("Skip (demo)", "try|skip")]


def test_start_from_a_stranger_starts_the_demo(deps, trial):
    run(deps, trial, msg("/start"))
    assert "Saans demo" in deps.messenger.to(STRANGER)[0]


def test_demo_approve_shows_previews_only_to_the_tester(deps, trial):
    run(deps, trial, msg("/try"))
    run(deps, trial, tap("try|approve"))
    texts = deps.messenger.to(STRANGER)
    assert any(t.startswith("<b>Parents' channel would get:</b>") for t in texts)
    assert any(t.startswith("<b>Staff group would get:</b>") for t in texts)
    assert "Would your school use this?" in texts[-1]
    for chat in (SETTINGS.parent_channel_id, SETTINGS.staff_group_id,
                 SETTINGS.demo_parent_channel_id, SETTINGS.demo_staff_group_id):
        assert deps.messenger.to(chat) == []                     # nothing leaves the chat
    assert "Approved (demo)" in deps.messenger.edits[-1][2]


def test_rate_limit_three_per_day(deps, trial):
    for _ in range(4):
        run(deps, trial, msg("/try"))
    assert deps.messenger.to(STRANGER)[-1] == LIMIT_REACHED
    trial.now = lambda: datetime(2026, 10, 7, 9, tzinfo=IST)     # next day resets
    run(deps, trial, msg("/try"))
    assert "Saans demo" in deps.messenger.to(STRANGER)[-2]


def test_feedback_answer_comment_and_report(deps, trial):
    run(deps, trial, msg("/try"))
    run(deps, trial, tap("try|approve"))
    run(deps, trial, tap("fb|yes"))
    assert "Reply with one line" in deps.messenger.to(STRANGER)[-1]
    run(deps, trial, msg("Our PE teacher decides this every morning by guesswork"))
    assert deps.messenger.to(STRANGER)[-1].startswith("Thank you")
    run(deps, trial, msg("another message"))                     # not stored twice
    assert deps.messenger.to(STRANGER)[-1] == PUBLIC_HELP

    other = 5151
    run(deps, trial, msg("/try", user=other))
    run(deps, trial, tap("fb|maybe", user=other))

    admin = next(iter(SETTINGS.admin_user_ids))
    run(deps, trial, msg("/feedback", user=admin))
    report = deps.messenger.to(admin)[-1]
    assert "2 people tried the demo · 2 answered" in report
    assert "Yes: 1 · Maybe: 1 · No: 0" in report
    assert "[Yes] Our PE teacher decides this every morning by guesswork" in report


def test_feedback_command_is_admin_only(deps, trial):
    run(deps, trial, msg("/feedback"))
    assert "feedback" not in deps.messenger.to(STRANGER)[-1].lower() or \
        deps.messenger.to(STRANGER)[-1] == PUBLIC_HELP


def test_demo_callbacks_never_touch_real_plans(deps, trial):
    run(deps, trial, tap("try|approve"))
    assert deps.store.get(SETTINGS.school_id, datetime(2025, 11, 11).date(), "replay") is None


def test_try_is_ignored_in_group_chats(deps, trial):
    run(deps, trial, msg("/try", chat_type="group"))
    assert deps.messenger.sent == []


def test_stranger_plain_message_gets_public_help(deps, trial):
    run(deps, trial, msg("hello"))
    assert deps.messenger.to(STRANGER) == [PUBLIC_HELP]


def test_unknown_demo_button(deps, trial):
    run(deps, trial, tap("fb|maybe-not"))
    assert deps.messenger.answers[-1][1] == "Unknown button."
