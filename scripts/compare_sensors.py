"""Compare candidate PM2.5 sensors per station over one IST day, to pick one per station.

Usage: python scripts/compare_sensors.py 2025-11-10
"""

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saans.ingest import IST, AlignmentError, OpenAQClient, ist_hour  # noqa: E402
from scripts.env import load_env  # noqa: E402

CANDIDATES = {
    "Anand Vihar": [384, 12235610],
    "ITO": [12234753, 14878],
    "RK Puram": [35, 12234787],
    "Punjabi Bagh": [396, 12234796],
    "Mandir Marg": [17770, 12234778],
    "Lodhi Road (IMD)": [14984, 12234714],
}


def main() -> None:
    load_env()
    day = date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2025-11-10")
    start = datetime.combine(day, datetime.min.time(), IST)
    client = OpenAQClient(os.environ.get("OPENAQ_API_KEY", ""))
    for station, sensor_ids in CANDIDATES.items():
        for sensor_id in sensor_ids:
            items = list(client.sensor_hours(sensor_id, start, start + timedelta(days=1)))
            values = [i["value"] for i in items if i.get("value") is not None]
            starts = [s for s in ((i.get("period") or {}).get("datetimeFrom", {}).get("utc")
                                  for i in items) if s]
            try:
                aligned = bool(starts) and all(ist_hour(s) for s in starts)
            except AlignmentError:
                aligned = False
            mean = sum(values) / len(values) if values else float("nan")
            first = starts[0] if starts else "-"
            print(f"{station:18} {sensor_id:>9}  hours={len(values):2}  mean={mean:7.1f}  "
                  f"IST-aligned={aligned}  first={first}")


if __name__ == "__main__":
    main()
