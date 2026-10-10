from datetime import date, datetime, time
from pathlib import Path

from backtest.metrics import (
    DayResult, Score, afternoon_is_cleaner, gate_a, gate_b, score, select_method,
)
from saans.bands import Band
from saans.calendar import load_calendar
from saans.ingest import IST

ROOT = Path(__file__).resolve().parent.parent
D = date(2025, 11, 12)


def test_calendar_2025_excludes_holidays_and_sundays():
    cal = load_calendar(ROOT / "backtest" / "calendar_2025.yaml")
    assert not cal.is_school_day(date(2025, 10, 20))   # Diwali
    assert not cal.is_school_day(date(2025, 10, 27))   # Chhath (notified)
    assert not cal.is_school_day(date(2025, 11, 25))   # GTB Shaheedi Diwas
    assert not cal.is_school_day(date(2025, 11, 9))    # Sunday
    assert cal.is_school_day(date(2025, 11, 8))        # Saturday
    assert cal.is_school_day(date(2025, 11, 11))


def r(today, predicted, actual):
    return DayResult(D, today, predicted, actual)


def test_score_counts_and_transitions():
    results = [
        r(200, 200, 200),   # TP, no transition
        r(200, 200, 100),   # FP, transition (VP -> Poor), wrong
        r(100, 100, 200),   # FN, transition, wrong
        r(100, 200, 200),   # TP, transition, right
        r(100, 100, 100),   # TN
    ]
    s = score("m", results, Band.VERY_POOR)
    assert (s.tp, s.fp, s.fn, s.tn) == (2, 1, 1, 1)
    assert s.hit_rate == 2 / 3 and s.false_alarm_ratio == 1 / 3
    assert s.base_rate == 3 / 5
    assert (s.transition_days, s.transition_correct) == (3, 1)


def make(method, tp, fp, fn, tn, tdays, tright):
    return Score(method, Band.VERY_POOR, tp, fp, fn, tn, tdays, tright)


def test_selection_prefers_best_hit_rate_within_far_cap():
    scores = {
        "persistence": make("persistence", 8, 2, 2, 8, 5, 0),     # hit .80 far .20
        "baseline": make("baseline", 9, 3, 1, 7, 5, 2),           # hit .90 far .25
    }
    assert select_method(scores) == "baseline"


def test_selection_rejects_method_over_far_cap():
    scores = {
        "persistence": make("persistence", 8, 2, 2, 8, 5, 0),
        "baseline": make("baseline", 10, 6, 0, 4, 5, 3),          # far .375
    }
    assert select_method(scores) == "persistence"


def test_selection_rejects_method_worse_on_transitions():
    scores = {
        "persistence": make("persistence", 8, 2, 2, 8, 5, 2),
        "baseline": make("baseline", 9, 1, 1, 9, 5, 1),
    }
    assert select_method(scores) == "persistence"


def test_selection_none_when_nothing_qualifies():
    scores = {"persistence": make("persistence", 1, 9, 0, 0, 0, 0)}
    assert select_method(scores) is None


def test_gate_a_thresholds():
    assert gate_a(make("m", 7, 3, 3, 0, 0, 0))            # hit .70, far .30
    assert not gate_a(make("m", 6, 0, 4, 0, 0, 0))        # hit .60
    assert not gate_a(make("m", 7, 4, 3, 0, 0, 0))        # far .36


def curve(values_by_hour):
    return {datetime.combine(D, time(h), IST): v for h, v in values_by_hour.items()}


def test_check_b_one_band_drop_passes():
    # 3h means: 08 ~ Severe (300), 13 ~ Very Poor (150)
    hourly = curve({6: 300, 7: 300, 8: 300, 9: 300, 10: 200, 11: 150, 12: 150, 13: 150})
    assert afternoon_is_cleaner(hourly, D) is True


def test_check_b_same_band_fails_even_if_pm_halves():
    # 240 -> 130: both Very Poor, so no band step
    hourly = curve({6: 240, 7: 240, 8: 240, 9: 240, 10: 180, 11: 130, 12: 130, 13: 130})
    assert afternoon_is_cleaner(hourly, D) is False


def test_check_b_unknown_when_hours_missing():
    hourly = curve({6: 300, 7: 300, 8: 300, 9: 300})
    assert afternoon_is_cleaner(hourly, D) is None


def test_gate_b_ignores_unknown_days():
    assert gate_b([True, False, None, True]) == (2 / 3, 3)
    assert gate_b([None]) == (None, 0)
