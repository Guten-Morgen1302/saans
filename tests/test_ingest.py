from datetime import datetime, timedelta, timezone

import pytest
import responses
from responses import matchers

from saans.ingest import (
    BASE_URL,
    IST,
    PAGE_LIMIT,
    AlignmentError,
    OpenAQClient,
    OpenAQError,
    RateLimitError,
    fetch_series,
    ist_hour,
    station_mean,
    to_series,
)

HOURS_URL = f"{BASE_URL}/sensors/42/hours"
START = datetime(2025, 11, 10, tzinfo=IST)
END = datetime(2025, 11, 11, tzinfo=IST)


def hour_item(utc_start: str, value):
    return {"value": value, "period": {"datetimeFrom": {"utc": utc_start, "local": ""}}}


def ist_aligned_items(n: int, first=datetime(2025, 11, 9, 18, 30, tzinfo=timezone.utc)):
    return [hour_item((first + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), 100.0 + i)
            for i in range(n)]


@pytest.fixture
def client():
    sleeps = []
    c = OpenAQClient("test-key", sleep=sleeps.append)
    c.sleeps = sleeps
    return c


# --- hour convention -------------------------------------------------------

def test_ist_hour_maps_utc_period_start_to_ist_clock_hour():
    assert ist_hour("2025-11-10T02:30:00Z") == datetime(2025, 11, 10, 8, tzinfo=IST)


def test_ist_hour_crosses_midnight():
    assert ist_hour("2025-11-10T18:30:00Z") == datetime(2025, 11, 11, 0, tzinfo=IST)
    assert ist_hour("2025-11-10T17:30:00Z") == datetime(2025, 11, 10, 23, tzinfo=IST)


def test_ist_hour_rejects_utc_aligned_periods():
    with pytest.raises(AlignmentError):
        ist_hour("2025-11-10T03:00:00Z")  # 08:30 IST: would shift the series by 30 min


# --- cleaning --------------------------------------------------------------

def test_to_series_drops_null_negative_and_duplicate_hours():
    items = [
        hour_item("2025-11-10T02:30:00Z", 180.0),
        hour_item("2025-11-10T02:30:00Z", 999.0),  # duplicate hour: first one wins
        hour_item("2025-11-10T03:30:00Z", None),
        {"value": 50.0, "period": None},
        hour_item("2025-11-10T04:30:00Z", -3.0),
        hour_item("2025-11-10T05:30:00Z", 0.0),  # zero is a valid reading
    ]
    s = to_series(42, iter(items))
    assert s.values == {
        datetime(2025, 11, 10, 8, tzinfo=IST): 180.0,
        datetime(2025, 11, 10, 11, tzinfo=IST): 0.0,
    }
    assert (s.dropped_null, s.dropped_negative, s.duplicates) == (2, 1, 1)


def test_station_mean_counts_only_reporting_sensors():
    a = to_series(1, iter([hour_item("2025-11-10T02:30:00Z", 100.0),
                           hour_item("2025-11-10T03:30:00Z", 200.0)]))
    b = to_series(2, iter([hour_item("2025-11-10T02:30:00Z", 300.0)]))
    means = station_mean({1: a, 2: b})
    assert means[datetime(2025, 11, 10, 8, tzinfo=IST)] == (200.0, 2)
    assert means[datetime(2025, 11, 10, 9, tzinfo=IST)] == (200.0, 1)


# --- HTTP: pagination, rate limits, errors ----------------------------------

@responses.activate
def test_sensor_hours_follows_pagination_until_short_page(client):
    params = {"datetime_from": "2025-11-09T18:30:00Z", "datetime_to": "2025-11-10T18:30:00Z",
              "limit": str(PAGE_LIMIT)}
    page1 = ist_aligned_items(PAGE_LIMIT)
    page2 = ist_aligned_items(5, first=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))
    responses.get(HOURS_URL, json={"results": page1},
                  match=[matchers.query_param_matcher({**params, "page": "1"})])
    responses.get(HOURS_URL, json={"results": page2},
                  match=[matchers.query_param_matcher({**params, "page": "2"})])

    items = list(client.sensor_hours(42, START, END))

    assert len(items) == PAGE_LIMIT + 5
    assert len(responses.calls) == 2
    assert responses.calls[0].request.headers["X-API-Key"] == "test-key"


@responses.activate
def test_429_waits_for_reset_then_succeeds(client):
    responses.get(HOURS_URL, status=429, headers={"x-ratelimit-reset": "17"})
    responses.get(HOURS_URL, json={"results": ist_aligned_items(3)})

    items = list(client.sensor_hours(42, START, END))

    assert len(items) == 3
    assert client.sleeps == [18]


@responses.activate
def test_429_gives_up_after_three_retries(client):
    for _ in range(4):
        responses.get(HOURS_URL, status=429, headers={"x-ratelimit-reset": "5"})

    with pytest.raises(RateLimitError):
        list(client.sensor_hours(42, START, END))
    assert client.sleeps == [6, 6, 6]
    assert len(responses.calls) == 4


@responses.activate
def test_429_without_reset_header_waits_a_full_minute(client):
    responses.get(HOURS_URL, status=429)
    responses.get(HOURS_URL, json={"results": []})
    list(client.sensor_hours(42, START, END))
    assert client.sleeps == [61]


@responses.activate
def test_bad_key_raises_clear_error(client):
    responses.get(HOURS_URL, status=401, json={"detail": "unauthorized"})
    with pytest.raises(OpenAQError, match="OPENAQ_API_KEY"):
        list(client.sensor_hours(42, START, END))


@responses.activate
def test_server_error_is_not_retried(client):
    responses.get(HOURS_URL, status=502, body="bad gateway")
    with pytest.raises(OpenAQError, match="502"):
        list(client.sensor_hours(42, START, END))
    assert len(responses.calls) == 1


def test_empty_key_rejected():
    with pytest.raises(OpenAQError, match="empty"):
        OpenAQClient("")


def test_naive_datetime_rejected(client):
    with pytest.raises(ValueError, match="naive"):
        list(client.sensor_hours(42, datetime(2025, 11, 10), END))


@responses.activate
def test_fetch_series_end_to_end(client):
    responses.get(HOURS_URL, json={"results": ist_aligned_items(24)})
    out = fetch_series(client, [42], START, END)
    assert len(out[42].values) == 24
    assert min(out[42].values) == datetime(2025, 11, 10, 0, tzinfo=IST)
