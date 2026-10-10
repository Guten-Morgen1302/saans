"""Backfill hourly PM2.5 for the configured stations into data/raw/pm25_hourly.csv.

Usage: python scripts/backfill.py --from 2025-10-01 --to 2026-01-01
Dates are IST calendar days; --to is exclusive.
"""

import argparse
import csv
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from saans.ingest import IST, OpenAQClient, fetch_series  # noqa: E402
from scripts.env import load_env  # noqa: E402


def load_sensor_ids(path: Path = ROOT / "config" / "stations.yaml") -> dict[int, str]:
    stations = yaml.safe_load(path.read_text(encoding="utf-8"))["stations"]
    missing = [s["name"] for s in stations if not s.get("sensor_id")]
    if missing:
        sys.exit(f"config/stations.yaml has no sensor_id for: {', '.join(missing)}. "
                 "Run scripts/find_sensors.py and fill them in.")
    return {int(s["sensor_id"]): s["name"] for s in stations}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "pm25_hourly.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    load_env()
    names = load_sensor_ids()
    client = OpenAQClient(os.environ.get("OPENAQ_API_KEY", ""))
    start = datetime.combine(args.start, datetime.min.time(), IST)
    end = datetime.combine(args.end, datetime.min.time(), IST)
    series = fetch_series(client, list(names), start, end)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["hour_ist", "sensor_id", "station", "pm25"])
        rows = 0
        for sensor_id, s in series.items():
            for hour, value in sorted(s.values.items()):
                writer.writerow([hour.isoformat(), sensor_id, names[sensor_id], value])
                rows += 1
    print(f"wrote {rows} rows to {args.out}")


if __name__ == "__main__":
    main()
