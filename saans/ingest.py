"""OpenAQ v3 ingest: hourly PM2.5 per CPCB sensor, bucketed into IST clock hours.

Hour convention (tested): a value belongs to the IST hour in which its period
*starts*. OpenAQ's period for hour H covers [H, H+1), so a reading with
period.datetimeFrom.utc = 2025-11-10T02:30:00Z is IST 08:00 on 2025-11-10.

If a sensor's hourly periods are aligned to UTC hours rather than IST hours,
every period starts at :30 IST and cannot be placed in an IST clock hour
without shifting it by half an hour. That raises AlignmentError instead of
silently moving the whole series.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.openaq.org/v3"
IST = timezone(timedelta(hours=5, minutes=30))
PAGE_LIMIT = 1000
MAX_RETRIES = 3


class OpenAQError(RuntimeError):
    """Non-retryable API failure (auth, bad request, server error)."""


class RateLimitError(OpenAQError):
    """Still rate-limited after MAX_RETRIES waits."""


class AlignmentError(ValueError):
    """Hourly periods do not start on IST clock hours."""


class OpenAQClient:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ):
        if not api_key:
            raise OpenAQError("OPENAQ_API_KEY is empty")
        self.session = session or requests.Session()
        self.session.headers["X-API-Key"] = api_key
        self.base_url = base_url
        self.sleep = sleep
        self.timeout = timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(MAX_RETRIES + 1):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429:
                if attempt == MAX_RETRIES:
                    raise RateLimitError(f"429 after {MAX_RETRIES} retries: {path} {params}")
                wait = _reset_seconds(resp) + 1
                log.warning("openaq 429 on %s (attempt %d), waiting %ss", path, attempt + 1, wait)
                self.sleep(wait)
                continue
            if resp.status_code in (401, 403):
                raise OpenAQError(f"{resp.status_code} from OpenAQ: check OPENAQ_API_KEY")
            if not resp.ok:
                raise OpenAQError(f"{resp.status_code} from OpenAQ on {path}: {resp.text[:200]}")
            return resp.json()
        raise AssertionError("unreachable")

    def sensor_hours(self, sensor_id: int, start: datetime, end: datetime) -> Iterator[dict]:
        """Yield every hourly result for [start, end), following pagination."""
        page = 1
        while True:
            body = self.get(
                f"/sensors/{sensor_id}/hours",
                {
                    "datetime_from": _iso_utc(start),
                    "datetime_to": _iso_utc(end),
                    "limit": PAGE_LIMIT,
                    "page": page,
                },
            )
            results = body.get("results") or []
            yield from results
            if len(results) < PAGE_LIMIT:
                return
            page += 1


def _reset_seconds(resp: requests.Response) -> float:
    try:
        return max(float(resp.headers.get("x-ratelimit-reset", "60")), 0.0)
    except ValueError:
        return 60.0


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime {dt!r}; pass an aware datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ist_hour(period_start_utc: str) -> datetime:
    """IST clock hour a period starting at `period_start_utc` belongs to."""
    start = datetime.fromisoformat(period_start_utc.replace("Z", "+00:00")).astimezone(IST)
    if start.minute or start.second:
        raise AlignmentError(f"period starts at {start.isoformat()}, not on an IST hour")
    return start


@dataclass
class HourlySeries:
    sensor_id: int
    values: dict[datetime, float] = field(default_factory=dict)
    dropped_null: int = 0
    dropped_negative: int = 0
    duplicates: int = 0


def to_series(sensor_id: int, results: Iterator[dict]) -> HourlySeries:
    series = HourlySeries(sensor_id)
    for item in results:
        period = item.get("period") or {}
        start = (period.get("datetimeFrom") or {}).get("utc")
        value = item.get("value")
        if start is None or value is None:
            series.dropped_null += 1
            continue
        if value < 0:
            series.dropped_negative += 1
            continue
        hour = ist_hour(start)
        if hour in series.values:
            series.duplicates += 1
            continue
        series.values[hour] = float(value)
    return series


def fetch_series(
    client: OpenAQClient, sensor_ids: list[int], start: datetime, end: datetime
) -> dict[int, HourlySeries]:
    out = {}
    for sensor_id in sensor_ids:
        series = to_series(sensor_id, client.sensor_hours(sensor_id, start, end))
        log.info(
            "sensor %s: %d hours, %d null, %d negative, %d duplicate",
            sensor_id, len(series.values), series.dropped_null,
            series.dropped_negative, series.duplicates,
        )
        out[sensor_id] = series
    return out


def station_mean(series_by_sensor: dict[int, HourlySeries]) -> dict[datetime, tuple[float, int]]:
    """Per IST hour: (mean PM2.5 across sensors that reported, number of sensors)."""
    sums: dict[datetime, list[float]] = {}
    for series in series_by_sensor.values():
        for hour, value in series.values.items():
            sums.setdefault(hour, []).append(value)
    return {hour: (sum(vals) / len(vals), len(vals)) for hour, vals in sorted(sums.items())}
