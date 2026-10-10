"""Fit and check the backup-sensor correction used in config/stations.yaml.

For each candidate sensor: correlation with the CPCB 6-station mean and the
median ratio (sensor / CPCB, hours with CPCB > 20). The correction is fitted on
Oct 15-Nov 15 2025 and tested on Nov 17-Dec 15 2025 (school days).

Usage: python scripts/fit_fallback.py
"""

import os
import statistics
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from saans.bands import Band, band  # noqa: E402
from saans.calendar import load_calendar  # noqa: E402
from saans.forecast import Unavailable, actual_school_level, forecast  # noqa: E402
from saans.ingest import IST, OpenAQClient, fetch_series  # noqa: E402
from saans.pipeline import load_series_csv  # noqa: E402
from scripts.env import load_env  # noqa: E402

CANDIDATES = {9320367: "GK1", 14453757: "Noida ext", 14745878: "Anand Lok", 13273790: "Vasant Kunj"}
SPLIT = datetime(2025, 11, 16, tzinfo=IST)


def main() -> None:
    load_env()
    cpcb = load_series_csv()
    got = fetch_series(OpenAQClient(os.environ["OPENAQ_API_KEY"]), list(CANDIDATES),
                       datetime(2025, 10, 15, tzinfo=IST), datetime(2025, 12, 16, tzinfo=IST))
    ratios = {}
    for sid, s in got.items():
        pairs = [(cpcb[h][0], v) for h, v in s.values.items() if h in cpcb]
        if len(pairs) < 50:
            print(f"{CANDIDATES[sid]:12} too little overlap ({len(pairs)} h)")
            continue
        corr = statistics.correlation([a for a, _ in pairs], [b for _, b in pairs])
        fit = [v / cpcb[h][0] for h, v in s.values.items() if h in cpcb and cpcb[h][0] > 20 and h < SPLIT]
        ratios[sid] = statistics.median(fit or [b / a for a, b in pairs if a > 20])
        print(f"{CANDIDATES[sid]:12} corr {corr:.2f}  divide_by {ratios[sid]:.2f}")

    keep = [sid for sid in ratios if sid != 13273790]
    fb = {}
    for h in set().union(*(got[s].values for s in keep)):
        vals = [got[s].values[h] / ratios[s] for s in keep if h in got[s].values]
        fb[h] = (sum(vals) / len(vals), len(vals))
    cal = load_calendar(ROOT / "backtest" / "calendar_2025.yaml")
    tp = fa = fn = 0
    d = date(2025, 11, 17)
    while d <= date(2025, 12, 15):
        actual = actual_school_level(cpcb, d)
        if cal.is_school_day(d) and actual:
            try:
                f = forecast(fb, datetime.combine(d - timedelta(days=1), time(18), IST),
                             persistence=True, min_stations=1)
                p, a = band(f.level) >= Band.VERY_POOR, band(actual) >= Band.VERY_POOR
                tp += p and a
                fa += p and not a
                fn += a and not p
            except Unavailable:
                pass
        d += timedelta(days=1)
    print(f"held-out >= Very Poor: hit {tp / max(tp + fn, 1):.0%}, false alarms {fa / max(tp + fa, 1):.0%}")


if __name__ == "__main__":
    main()
