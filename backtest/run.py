"""Backtest + go/no-go (T3): every school day Oct 15 - Dec 15, 2025.

For each target school day, the forecast is made at 18:00 IST the evening
before, using only hours published by 16:00 (saans.forecast.as_of). Scores
are computed on school days only (eng review R11).

Usage: python backtest/run.py [--csv data/raw/pm25_hourly.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.metrics import (  # noqa: E402
    CHECK_B_SHARE, DayResult, afternoon_is_cleaner, gate_a, gate_b, score, select_method,
)
from saans.bands import Band, band  # noqa: E402
from saans.calendar import load_calendar  # noqa: E402
from saans.forecast import Series, Unavailable, actual_school_level, forecast  # noqa: E402
from saans.ingest import IST  # noqa: E402

START, END = date(2025, 10, 15), date(2025, 12, 15)
METHODS = {"baseline": False, "persistence": True}


def load_series(path: Path) -> Series:
    by_hour: dict[datetime, list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_hour[datetime.fromisoformat(row["hour_ist"])].append(float(row["pm25"]))
    return {h: (sum(v) / len(v), len(v)) for h, v in by_hour.items()}


def run(series: Series, cal) -> dict:
    results = {m: [] for m in METHODS}
    excluded = {"not_school_day": 0, "forecast_unavailable": [], "actual_missing": []}
    replay, check_b_actual, check_b_forecast = [], [], []

    day = START
    while day <= END:
        if not cal.is_school_day(day):
            excluded["not_school_day"] += 1
            day += timedelta(days=1)
            continue
        made_at = datetime.combine(day - timedelta(days=1), time(18), IST)
        actual = actual_school_level(series, day)
        forecasts = {}
        try:
            for method, persistence in METHODS.items():
                forecasts[method] = forecast(series, made_at, persistence=persistence)
        except Unavailable as e:
            excluded["forecast_unavailable"].append(f"{day}: {e}")
            day += timedelta(days=1)
            continue
        if actual is None:
            excluded["actual_missing"].append(str(day))
        else:
            for method, f in forecasts.items():
                results[method].append(DayResult(day, f.level_today, f.level, actual))
            if band(actual) in (Band.POOR, Band.VERY_POOR):
                hourly_actual = {h: v for h, (v, n) in series.items() if n >= 2 and h.date() == day}
                check_b_actual.append(afternoon_is_cleaner(hourly_actual, day))
                check_b_forecast.append(afternoon_is_cleaner(forecasts["baseline"].hourly, day))
        if date(2025, 11, 7) <= day <= date(2025, 11, 14):
            f = forecasts["baseline"]
            replay.append({
                "target": str(day), "made_at": made_at.isoformat(),
                "hours_used_today": f.today_hours_used,
                "forecast": round(f.level), "forecast_band": band(f.level).label,
                "persistence_band": band(forecasts["persistence"].level).label,
                "actual": round(actual) if actual else None,
                "actual_band": band(actual).label if actual else None,
            })
        day += timedelta(days=1)

    scores = {t.label: {m: score(m, r, t) for m, r in results.items()}
              for t in (Band.VERY_POOR, Band.POOR)}
    vp = scores[Band.VERY_POOR.label]
    chosen = select_method(vp)
    b_share, b_n = gate_b(check_b_actual)
    bf_share, bf_n = gate_b(check_b_forecast)
    return {
        "window": [str(START), str(END)],
        "scored_days": len(results["baseline"]),
        "excluded": excluded,
        "scores": {t: {m: _score_dict(s) for m, s in by.items()} for t, by in scores.items()},
        "selected_method": chosen,
        "gate_a": {"method": chosen, "pass": bool(chosen and gate_a(vp[chosen]))},
        "gate_b": {"share_actual": b_share, "days_actual": b_n,
                   "share_forecast": bf_share, "days_forecast": bf_n,
                   "pass": b_share is not None and b_share >= CHECK_B_SHARE},
        "replay": replay,
        "days": {m: [vars(r) | {"target": str(r.target)} for r in rs] for m, rs in results.items()},
    }


def _score_dict(s) -> dict:
    return {"tp": s.tp, "fp": s.fp, "fn": s.fn, "tn": s.tn, "hit_rate": s.hit_rate,
            "false_alarm_ratio": s.false_alarm_ratio, "base_rate": s.base_rate,
            "transition_days": s.transition_days, "transition_accuracy": s.transition_accuracy}


def _pct(x) -> str:
    return "  n/a" if x is None else f"{x:5.0%}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "raw" / "pm25_hourly.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "backtest" / "results.json")
    args = parser.parse_args()

    out = run(load_series(args.csv), load_calendar(ROOT / "backtest" / "calendar_2025.yaml"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")

    ex = out["excluded"]
    print(f"Window {START}..{END}: {out['scored_days']} school days scored; "
          f"{len(ex['forecast_unavailable'])} forecast unavailable, "
          f"{len(ex['actual_missing'])} actual missing")
    for t, by in out["scores"].items():
        print(f"\n>= {t}")
        for m, s in by.items():
            print(f"  {m:12} hit {_pct(s['hit_rate'])}  false alarms {_pct(s['false_alarm_ratio'])}  "
                  f"base rate {_pct(s['base_rate'])}  transitions {s['transition_days']:2} "
                  f"(right {_pct(s['transition_accuracy'])})  TP{s['tp']} FP{s['fp']} FN{s['fn']} TN{s['tn']}")
    ga, gb = out["gate_a"], out["gate_b"]
    print(f"\nSelected method (R6): {out['selected_method']}")
    print(f"Gate (a) hit >= 70% and FAR <= 30% at >= Very Poor: {'PASS' if ga['pass'] else 'FAIL'}")
    print(f"Gate (b) afternoon cleaner on Poor/VP days: {_pct(gb['share_actual'])} of "
          f"{gb['days_actual']} days (forecast says {_pct(gb['share_forecast'])}): "
          f"{'PASS' if gb['pass'] else 'FAIL'}")
    print("\nReplay (forecast made 18:00 the evening before):")
    for r in out["replay"]:
        print(f"  {r['target']}  used {r['hours_used_today']}/6  forecast {r['forecast']:4} "
              f"{r['forecast_band']:9}  actual {r['actual'] or '-':>4} {r['actual_band'] or ''}")
    if ex["forecast_unavailable"]:
        print("\nUnavailable:", *ex["forecast_unavailable"], sep="\n  ")


if __name__ == "__main__":
    main()
