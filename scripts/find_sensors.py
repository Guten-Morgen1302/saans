"""List PM2.5 sensors at CPCB locations near central Delhi, to fill config/stations.yaml.

Usage: python scripts/find_sensors.py   (reads OPENAQ_API_KEY from the environment or .env)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saans.ingest import OpenAQClient  # noqa: E402
from scripts.env import load_env  # noqa: E402

DELHI = "28.6139,77.2090"
PM25_PARAMETER_ID = 2


def main() -> None:
    load_env()
    client = OpenAQClient(os.environ.get("OPENAQ_API_KEY", ""))
    body = client.get(
        "/locations",
        {"coordinates": DELHI, "radius": 25000, "parameters_id": PM25_PARAMETER_ID, "limit": 1000},
    )
    for loc in sorted(body.get("results", []), key=lambda l: l["name"]):
        provider = (loc.get("provider") or {}).get("name", "?")
        pm25 = [s for s in loc.get("sensors", []) if s["parameter"]["name"] == "pm25"]
        ids = ", ".join(str(s["id"]) for s in pm25) or "-"
        last = (loc.get("datetimeLast") or {}).get("local", "?")
        print(f"{loc['name'][:45]:45}  {provider[:12]:12}  pm25 sensors: {ids:20}  last: {last}")


if __name__ == "__main__":
    main()
