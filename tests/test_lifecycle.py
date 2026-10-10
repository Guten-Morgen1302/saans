from datetime import date

import pytest

from saans import lifecycle
from saans.lifecycle import approve, callback_data, expire, parse_callback, remind, send_plan, skip
from tests.conftest import SETTINGS, TEXTS

DAY = date(2025, 11, 11)
SCHOOL = SETTINGS.school_id


def status(deps, mode="live"):
    return deps.store.get(SCHOOL, DAY, mode)["status"]


def test_send_creates_and_sends_with_buttons(deps):
    assert send_plan(deps, DAY, "live", TEXTS) == "sent"
    assert status(deps) == "SENT"
    chat, text, buttons = deps.messenger.sent[0]
    assert chat == SETTINGS.principal_chat_id and text == TEXTS["principal"]
    assert buttons == [("Approve & send", "approve|2025-11-11|live"),
                       ("Skip (send nothing)", "skip|2025-11-11|live")]


def test_retry_after_success_does_not_resend(deps):
    send_plan(deps, DAY, "live", TEXTS)
    assert send_plan(deps, DAY, "live", TEXTS) == "already_sent"
    assert len(deps.messenger.sent) == 1


def test_retry_resends_a_plan_saved_but_never_sent(deps):
    deps.messenger.fail_chats[SETTINGS.principal_chat_id] = 1
    with pytest.raises(lifecycle.MessengerError):
        send_plan(deps, DAY, "live", TEXTS)          # crashed after save
    assert status(deps) == "CREATED"
    assert send_plan(deps, DAY, "live", {"principal": "NEW TEXT", "parents": "", "staff": ""}) == "sent"
    assert deps.messenger.sent[-1][1] == TEXTS["principal"]   # the stored plan, not a new one
    assert status(deps) == "SENT"


def test_approve_posts_to_both_live_channels_once(deps):
    send_plan(deps, DAY, "live", TEXTS)
    assert approve(deps, DAY, "live", by=111) == "posted"
    assert deps.messenger.to(SETTINGS.parent_channel_id) == [TEXTS["parents"]]
    assert deps.messenger.to(SETTINGS.staff_group_id) == [TEXTS["staff"]]
    plan = deps.store.get(SCHOOL, DAY, "live")
    assert plan["status"] == "POSTED" and plan["delivery"] == {"parents": "POSTED", "staff": "POSTED"}
    assert [e["event"] for e in plan["events"]] == ["created", "sent", "approved", "posting", "posted"]


def test_double_tap_posts_once(deps):
    send_plan(deps, DAY, "live", TEXTS)
    approve(deps, DAY, "live", by=111)
    assert approve(deps, DAY, "live", by=111) == "already_done"
    assert len(deps.messenger.to(SETTINGS.parent_channel_id)) == 1


def test_channel_retries_then_succeeds(deps):
    send_plan(deps, DAY, "live", TEXTS)
    deps.messenger.fail_chats[SETTINGS.parent_channel_id] = 2
    assert approve(deps, DAY, "live", by=111) == "posted"


def test_channel_failure_after_three_attempts_tells_principal(deps):
    send_plan(deps, DAY, "live", TEXTS)
    deps.messenger.fail_chats[SETTINGS.staff_group_id] = 3
    assert approve(deps, DAY, "live", by=111) == "failed"
    plan = deps.store.get(SCHOOL, DAY, "live")
    assert plan["status"] == "FAILED"
    assert plan["delivery"] == {"parents": "POSTED", "staff": "FAILED"}
    forward = deps.messenger.to(SETTINGS.principal_chat_id)[-1]
    assert "staff group" in forward and TEXTS["staff"] in forward     # text to forward included
    final = deps.messenger.edits[-1][2]
    assert "Parents: sent" in final and "Staff: could not post" in final


def test_skip_sends_nothing_and_blocks_later_approve(deps):
    send_plan(deps, DAY, "live", TEXTS)
    assert skip(deps, DAY, "live", by=111) == "skipped"
    assert approve(deps, DAY, "live", by=111) == "already_skipped"
    assert deps.messenger.to(SETTINGS.parent_channel_id) == []


def test_reminder_only_while_waiting(deps):
    send_plan(deps, DAY, "live", TEXTS)
    assert remind(deps, DAY) == "reminded"
    approve(deps, DAY, "live", by=111)
    assert remind(deps, DAY) == "nothing_to_remind"


def test_expiry_stamps_the_plan_then_late_approve_is_rejected(deps):
    send_plan(deps, DAY, "live", TEXTS)
    assert expire(deps, DAY) == "expired"
    assert "Expired 07:00, nothing sent" in deps.messenger.edits[-1][2]
    assert approve(deps, DAY, "live", by=111) == "expired"
    assert deps.messenger.to(SETTINGS.parent_channel_id) == []


def test_expiry_loses_to_an_earlier_approve(deps):
    send_plan(deps, DAY, "live", TEXTS)
    approve(deps, DAY, "live", by=111)
    assert expire(deps, DAY) == "nothing_to_expire"
    assert status(deps) == "POSTED"


def test_replay_goes_only_to_demo_channels_and_can_rerun(deps):
    assert send_plan(deps, DAY, "replay", TEXTS) == "sent"
    assert approve(deps, DAY, "replay", by=111) == "posted"
    assert deps.messenger.to(SETTINGS.demo_parent_channel_id) == [TEXTS["parents"]]
    assert deps.messenger.to(SETTINGS.parent_channel_id) == []
    # rerun resets instead of colliding
    assert send_plan(deps, DAY, "replay", TEXTS) == "sent"
    assert status(deps, "replay") == "SENT"


def test_replay_never_reminded_or_expired(deps):
    send_plan(deps, DAY, "replay", TEXTS)
    assert remind(deps, DAY) == "nothing_to_remind"
    assert expire(deps, DAY) == "nothing_to_expire"
    assert status(deps, "replay") == "SENT"


def principal_msg(deps, mode="live"):
    return int(deps.store.get(SCHOOL, DAY, mode)["principal_msg_id"])


def test_approve_stamps_sending_then_final_on_the_plan_message(deps):
    send_plan(deps, DAY, "live", TEXTS)
    msg = principal_msg(deps)
    approve(deps, DAY, "live", by=111)
    stamps = [text for _, mid, text in deps.messenger.edits if mid == msg]
    assert "Sending…" in stamps[0]
    assert "Approved" in stamps[-1] and "Parents: sent · Staff: sent" in stamps[-1]
    assert all(s.startswith(TEXTS["principal"]) for s in stamps)   # plan kept, stamp appended


def test_on_accepted_runs_before_any_channel_post(deps):
    send_plan(deps, DAY, "live", TEXTS)
    calls = []
    approve(deps, DAY, "live", by=111, on_accepted=lambda: calls.append(len(deps.messenger.sent)))
    assert calls == [1]            # only the principal's plan had been sent at that point


def test_skip_stamps_the_plan(deps):
    send_plan(deps, DAY, "live", TEXTS)
    skip(deps, DAY, "live", by=111)
    assert "Skipped" in deps.messenger.edits[-1][2] and "nothing sent" in deps.messenger.edits[-1][2]


def test_edit_failure_never_changes_state_and_falls_back_to_a_message(deps):
    send_plan(deps, DAY, "live", TEXTS)
    deps.messenger.fail_edits = 2      # both "Sending…" and the final stamp edits fail
    assert approve(deps, DAY, "live", by=111) == "posted"
    assert status(deps) == "POSTED"
    assert "Approved" in deps.messenger.to(SETTINGS.principal_chat_id)[-1]   # fallback message


def test_replay_rerun_stamps_the_old_message_replaced(deps):
    send_plan(deps, DAY, "replay", TEXTS)
    old = principal_msg(deps, "replay")
    send_plan(deps, DAY, "replay", TEXTS)
    assert principal_msg(deps, "replay") != old
    assert any(mid == old and "Replaced by a newer replay" in text
               for _, mid, text in deps.messenger.edits)
    assert lifecycle.is_current(deps, DAY, "replay", old) is False
    assert lifecycle.is_current(deps, DAY, "replay", principal_msg(deps, "replay")) is True
    assert lifecycle.is_current(deps, date(2025, 1, 1), "replay", 5) is None


def test_reminder_text_uses_the_date(deps):
    send_plan(deps, DAY, "live", TEXTS)
    remind(deps, DAY)
    assert "Tue 11 Nov" in deps.messenger.to(SETTINGS.principal_chat_id)[-1]


def test_live_plans_cannot_be_reset(deps):
    with pytest.raises(ValueError):
        deps.store.reset(SCHOOL, DAY, "live", TEXTS)


def test_callback_data_round_trip_and_validation():
    data = callback_data("approve", DAY, "replay")
    assert len(data.encode()) <= 64          # Telegram's callback_data limit
    assert parse_callback(data) == ("approve", DAY, "replay")
    for bad in ("delete|2025-11-11|live", "approve|2025-11-11|prod", "approve|x|live", "junk"):
        with pytest.raises(ValueError):
            parse_callback(bad)
