"""Backtest scoring: hit rate, false-alarm ratio, selection rule, check (b).

Definitions (design doc "Scores", eng review R6/R8):
- Hit rate            = TP / (TP + FN)
- False-alarm ratio   = FP / (TP + FP)
- Transition days     = days where "at or above threshold" differs between
                        day D (today's level) and day D+1 (actual)
- Selection (R6)      = highest hit rate among methods with FAR <= 0.30 AND a
                        transition-day accuracy >= pure persistence's
- Check (b) (R8)      = on >= 50% of actual Poor/Very Poor school days, the
                        lowest trailing-3h band in hours 11-13 is at least one
                        step below the highest band in hours 08-09
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from saans.bands import Band, band, rolling3

MAX_FAR = 0.30
MIN_HIT = 0.70
CHECK_B_SHARE = 0.50


@dataclass(frozen=True)
class DayResult:
    target: date
    level_today: float     # L_D, what persistence predicts
    predicted: float       # method's L_hat
    actual: float          # school-hours mean on the target day


@dataclass
class Score:
    method: str
    threshold: Band
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    transition_days: int = 0
    transition_correct: int = 0

    @property
    def hit_rate(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def false_alarm_ratio(self) -> float | None:
        return self.fp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def base_rate(self) -> float | None:
        n = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.fn) / n if n else None

    @property
    def transition_accuracy(self) -> float | None:
        return self.transition_correct / self.transition_days if self.transition_days else None


def score(method: str, results: list[DayResult], threshold: Band) -> Score:
    s = Score(method, threshold)
    for r in results:
        pred = band(r.predicted) >= threshold
        act = band(r.actual) >= threshold
        today = band(r.level_today) >= threshold
        if pred and act:
            s.tp += 1
        elif pred:
            s.fp += 1
        elif act:
            s.fn += 1
        else:
            s.tn += 1
        if today != act:
            s.transition_days += 1
            s.transition_correct += pred == act
    return s


def select_method(scores: dict[str, Score], persistence: str = "persistence") -> str | None:
    """R6: best hit rate among methods passing the FAR cap and the transition guard."""
    floor = scores[persistence].transition_accuracy or 0.0
    eligible = [
        s for s in scores.values()
        if s.false_alarm_ratio is not None and s.false_alarm_ratio <= MAX_FAR
        and (s.transition_accuracy or 0.0) >= floor
        and s.hit_rate is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda s: (s.hit_rate, s.method != persistence)).method


def gate_a(s: Score) -> bool:
    return (s.hit_rate or 0) >= MIN_HIT and s.false_alarm_ratio is not None \
        and s.false_alarm_ratio <= MAX_FAR


def afternoon_is_cleaner(hourly: dict[datetime, float], day: date) -> bool | None:
    """Check (b) for one day; None if the needed hours are missing."""
    bands = {h.hour: band(v) for h, v in rolling3(hourly).items() if h.date() == day}
    morning = [bands[h] for h in (8, 9) if h in bands]
    afternoon = [bands[h] for h in (11, 12, 13) if h in bands]
    if not morning or not afternoon:
        return None
    return min(afternoon) <= max(morning) - 1


def gate_b(outcomes: list[bool | None]) -> tuple[float | None, int]:
    known = [o for o in outcomes if o is not None]
    if not known:
        return None, 0
    return sum(known) / len(known), len(known)
