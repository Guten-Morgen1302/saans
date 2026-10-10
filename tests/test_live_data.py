from datetime import datetime, timedelta, timezone

import responses

from saans import handlers, pipeline
from saans.ingest import BASE_URL, IST, OpenAQClient

MADE = datetime(2026, 10, 5, 18, tzinfo=IST)
BACKUP = pipeline.Backup(sensors={901: 1.2, 902: 1.0}, stale_after=timedelta(hours=6))


def hours_json(last_ist: datetime, n: int, value: float):
    """n hourly results ending at last_ist (IST-aligned periods)."""
    out = []
    for k in range(n):
        h = (last_ist - timedelta(hours=n - 1 - k)).astimezone(timezone.utc)
        out.append({"value": value, "period": {"datetimeFrom": {"utc": h.strftime("%Y-%m-%dT%H:%M:%SZ")}}})
    return {"results": out}


def client():
    return OpenAQClient("k", sleep=lambda s: None)


@responses.activate
def test_fresh_cpcb_is_used_and_backup_not_called():
    responses.get(f"{BASE_URL}/sensors/1/hours", json=hours_json(MADE - timedelta(hours=2), 48, 150.0))
    d = pipeline.live_data(client(), [1], MADE, BACKUP)
    assert d.source == "cpcb" and d.min_stations == 2
    assert all("/sensors/90" not in c.request.url for c in responses.calls)


@responses.activate
def test_stale_cpcb_switches_to_corrected_backup():
    responses.get(f"{BASE_URL}/sensors/1/hours", json=hours_json(MADE - timedelta(days=3), 24, 150.0))
    responses.get(f"{BASE_URL}/sensors/901/hours", json=hours_json(MADE - timedelta(hours=1), 48, 120.0))
    responses.get(f"{BASE_URL}/sensors/902/hours", json=hours_json(MADE - timedelta(hours=1), 48, 80.0))
    d = pipeline.live_data(client(), [1], MADE, BACKUP)
    assert d.source == "backup" and d.min_stations == 1
    assert d.latest == MADE - timedelta(hours=1)
    value, n = d.series[MADE - timedelta(hours=1)]
    assert n == 2 and value == (120.0 / 1.2 + 80.0) / 2      # 90: each sensor corrected first


@responses.activate
def test_no_cpcb_and_no_backup_data_is_none():
    for sid in (1, 901, 902):
        responses.get(f"{BASE_URL}/sensors/{sid}/hours", json={"results": []})
    d = pipeline.live_data(client(), [1], MADE, BACKUP)
    assert d.source == "none" and d.series == {}


@responses.activate
def test_without_backup_config_stale_cpcb_is_still_returned():
    responses.get(f"{BASE_URL}/sensors/1/hours", json=hours_json(MADE - timedelta(days=3), 24, 150.0))
    assert pipeline.live_data(client(), [1], MADE, None).source == "cpcb"


def test_stations_config_has_six_cpcb_and_a_corrected_backup():
    ids, backup = handlers.load_stations()
    assert len(ids) == 6
    assert backup and len(backup.sensors) == 3 and all(1.0 < f < 1.5 for f in backup.sensors.values())
    assert 13273790 not in backup.sensors                    # likely-indoor sensor excluded


def test_backup_note_appears_only_in_principal_text():
    from tests.test_messages import SCHOOL, SEVERE
    from saans import messages as m
    tx = m.render_plan(SEVERE, SCHOOL, backup_data=True)
    assert "backup sensors" in tx["principal"] and "बैकअप सेंसर" in tx["principal"]
    assert "backup" not in tx["parents"] and "backup" not in tx["staff"]
