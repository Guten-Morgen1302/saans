from datetime import date, datetime, time
from pathlib import Path

import pytest

from saans import pipeline
from saans.calendar import load_calendar
from saans.ingest import IST
from saans.rules import load_rules
from saans.school import load_school
from tests.conftest import SETTINGS

ROOT = Path(__file__).resolve().parent.parent
CTX = pipeline.Context(
    school=load_school(ROOT / "school.yaml"),
    calendar=load_calendar(ROOT / "backtest" / "calendar_2025.yaml",
                           ROOT / "config" / "calendar_2026.yaml"),
    rules=load_rules(ROOT / "rules.yaml")[0],
)
SERIES = pipeline.load_series_csv()


def principal_texts(deps):
    return deps.messenger.to(SETTINGS.principal_chat_id)


def test_packaged_replay_data_covers_q4_2025():
    assert min(SERIES).date() == date(2025, 10, 1) and max(SERIES).date() == date(2025, 12, 31)
    assert all(1 <= n <= 6 for _, n in SERIES.values())


def test_replay_nov_10_sends_the_severe_plan_to_the_principal(deps):
    assert pipeline.run_replay(deps, CTX, date(2025, 11, 10), SERIES) == "sent"
    text = principal_texts(deps)[0]
    assert text.startswith("REPLAY · using data from the evening of Mon 10 Nov 2025")
    assert "Tue 11 Nov · 🟤 Severe" in text and "Prepare: likely GRAP Stage III" in text
    plan = deps.store.get(SETTINGS.school_id, date(2025, 11, 11), "replay")
    assert plan["status"] == "SENT" and "3B" not in plan["texts"]["parents"]


def test_replay_ignores_the_school_calendar(deps):
    # Sat 8 Nov evening → Sun 9 Nov target: live would skip, replay still runs
    assert pipeline.run_replay(deps, CTX, date(2025, 11, 8), SERIES) in ("sent", "no-action")


def test_live_skips_when_tomorrow_is_not_a_school_day(deps):
    sat = datetime(2025, 11, 8, 18, tzinfo=IST)
    assert pipeline.run_evening(deps, CTX, SERIES, sat, "live") == "not-school-day"
    diwali_eve = datetime(2025, 10, 19, 18, tzinfo=IST)
    assert pipeline.run_evening(deps, CTX, SERIES, diwali_eve, "live") == "not-school-day"
    assert deps.messenger.sent == []


def test_missing_data_sends_no_plan_tonight(deps):
    mon = datetime(2025, 11, 10, 18, tzinfo=IST)
    assert pipeline.run_evening(deps, CTX, {}, mon, "live") == "unavailable"
    assert "no plan tonight" in principal_texts(deps)[0] and "SAMEER" in principal_texts(deps)[0]


def test_clean_day_sends_a_no_action_note_without_buttons(deps):
    clean = {h: (40.0, 6) for h in SERIES}              # Good air every hour
    mon = datetime(2025, 11, 10, 18, tzinfo=IST)
    assert pipeline.run_evening(deps, CTX, clean, mon, "live") == "no-action"
    chat, text, buttons = deps.messenger.sent[0]
    assert "no changes needed" in text and buttons is None


def test_live_run_is_idempotent(deps):
    mon = datetime(2025, 11, 10, 18, tzinfo=IST)
    assert pipeline.run_evening(deps, CTX, SERIES, mon, "live") == "sent"
    assert pipeline.run_evening(deps, CTX, SERIES, mon, "live") == "already_sent"
    assert "REPLAY" not in principal_texts(deps)[0]


def test_summary_is_used_when_valid(deps):
    ctx = pipeline.Context(CTX.school, CTX.calendar, CTX.rules,
                           model_call=lambda s, p: "A Severe morning: outdoor activity is cancelled.")
    pipeline.run_replay(deps, ctx, date(2025, 11, 10), SERIES)
    assert "<i>A Severe morning: outdoor activity is cancelled.</i>" in principal_texts(deps)[0]


def test_evening_made_at_is_18_ist():
    assert pipeline.evening_made_at(datetime(2026, 10, 5, 12, 30, tzinfo=IST)) == \
        datetime(2026, 10, 5, 18, tzinfo=IST)
