import re

from backtest import report


def test_report_builds_self_contained_with_the_real_numbers(tmp_path):
    out = report.build(tmp_path / "index.html")
    page = out.read_text(encoding="utf-8")
    assert "Saans warned the evening before the official order." in page
    assert "at least\n<strong class=\"num\">15 hours</strong>" in page
    assert "86%" in page and "11%" in page                       # >= Very Poor, persistence
    assert "persistence (used)" in page
    assert page.count("<svg") == 2                               # wide + phone chart
    assert "not the official AQI" in page
    # self-contained: fonts embedded, no external scripts/styles/fonts
    assert "data:font/woff2;base64," in page
    assert not re.search(r'<(script|link)[^>]+(src|href)="https?://', page)
    assert "fonts.googleapis" not in page and "cdn." not in page


def test_lead_time_uses_the_trigger_reading_not_the_report_time():
    hours, order = report.lead_time([
        {"date": report.TARGET, "not_before": "09:00", "reported_by": "11:58"}])
    assert hours == 15
