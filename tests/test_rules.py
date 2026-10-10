from datetime import date, datetime, time
from pathlib import Path

import pytest

from saans.bands import Band
from saans.ingest import IST
from saans.rules import load_rules, protocol
from saans.school import Activity, School, load_school

ROOT = Path(__file__).resolve().parent.parent
RULES, URLS = load_rules(ROOT / "rules.yaml")
TUE = date(2025, 11, 11)
SUN = date(2025, 11, 9)


def school(*acts, asthma=None):
    return School("t", "Test", asthma or {"3B": 4, "5A": 0}, frozenset(range(6)), tuple(acts))


def pe(who, start, end):
    return Activity("PE", who, time.fromisoformat(start), time.fromisoformat(end), True)


def flat(value, day=TUE, hours=range(5, 15)):
    return {datetime.combine(day, time(h), IST): float(value) for h in hours}


def curve(values_by_hour, day=TUE):
    return {datetime.combine(day, time(h), IST): float(v) for h, v in values_by_hour.items()}


def by_who(plan):
    return {(a.who, a.activity): a for a in plan.actions if a.activity}


# --- rules file ------------------------------------------------------------

def test_every_rule_is_sourced_and_adaptations_cite_two():
    assert RULES
    for r in RULES:
        assert r.sources and all(s in URLS for s in r.sources)
        if r.kind == "adaptation":
            assert len(r.sources) >= 2


def test_unknown_source_is_rejected(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("sources: {a: http://x}\nrules:\n  - {id: r, kind: sourced, trigger: POOR, "
                   "applies_to: all_pe, action: indoor, template: t, sources: [b]}\n")
    with pytest.raises(ValueError, match="unknown sources"):
        load_rules(bad)


# --- per band x asthma / non-asthma ----------------------------------------

S = school(pe("3B", "09:00", "09:40"), pe("5A", "09:00", "09:40"))


@pytest.mark.parametrize("pm25,asthma_class,other_class", [
    (80, None, None),                          # Moderate: nothing
    (110, "indoor", None),                     # Poor: only the asthma class
    (200, "indoor", "indoor"),                 # Very Poor: all PE indoors
    (300, "cancel", "cancel"),                 # Severe: cancel, never just indoors
])
def test_pe_action_by_band(pm25, asthma_class, other_class):
    plan = protocol(flat(pm25), S, TUE, RULES)
    acts = by_who(plan)
    assert (acts.get(("3B", "PE")).action if ("3B", "PE") in acts else None) == asthma_class
    assert (acts.get(("5A", "PE")).action if ("5A", "PE") in acts else None) == other_class


def test_severe_asthma_class_is_cancelled_not_moved_indoors():
    # R2 regression: lowest trigger (Poor) flags it, strictest met rule (Severe) acts.
    a = by_who(protocol(flat(300), S, TUE, RULES))[("3B", "PE")]
    assert a.action == "cancel" and a.rule_id == "outdoor-cancel" and a.band is Band.SEVERE


def test_activity_band_is_worst_overlapped_hour():
    # 09:00-09:40 overlaps hour 9 only; 10:00 is Severe but does not count.
    s = school(pe("5A", "09:00", "09:40"))
    hourly = curve({6: 100, 7: 100, 8: 100, 9: 100, 10: 900, 11: 900, 12: 100, 13: 100})
    assert "5A" not in {a.who for a in protocol(hourly, s, TUE, RULES).actions}
    s2 = school(pe("5A", "09:40", "10:20"))       # overlaps 9 and 10
    a = by_who(protocol(hourly, s2, TUE, RULES))[("5A", "PE")]
    assert a.action == "cancel"


def test_indoor_activities_and_non_school_days_are_ignored():
    maths = Activity("Maths", "5A", time(9), time(9, 40), False)
    assert not [a for a in protocol(flat(300), school(maths), TUE, RULES).actions if a.activity]
    assert not [a for a in protocol(flat(300, SUN), school(pe("5A", "09:00", "09:40")),
                                   SUN, RULES).actions if a.activity]


def test_school_events_go_indoors_at_very_poor():
    assembly = Activity("Assembly", "school", time(8), time(8, 20), True)
    a = by_who(protocol(flat(200), school(assembly), TUE, RULES))[("school", "Assembly")]
    assert a.action == "indoor" and a.rule_id == "events-indoor"


# --- windows rule and header ------------------------------------------------

def test_windows_span_covers_hours_at_or_above_very_poor():
    hourly = curve({6: 300, 7: 300, 8: 300, 9: 300, 10: 300, 11: 100, 12: 100, 13: 100})
    plan = protocol(hourly, school(), TUE, RULES)
    w = [a for a in plan.actions if a.rule_id == "windows-closed"][0]
    # trailing 3h: 08-10 Severe, 11 = 233 VP, 12 = 167 VP, 13 = 100 Poor
    assert (w.start, w.end) == (time(8), time(13))


def test_no_windows_action_below_very_poor():
    plan = protocol(flat(110), school(), TUE, RULES)
    assert not [a for a in plan.actions if a.rule_id == "windows-closed"]


def test_header_names_the_worst_band_span():
    hourly = curve({6: 300, 7: 300, 8: 300, 9: 300, 10: 300, 11: 100, 12: 100, 13: 100})
    plan = protocol(hourly, school(), TUE, RULES)
    assert plan.header == (time(8), time(11), Band.SEVERE)
    assert plan.worst_band is Band.SEVERE and plan.worst_hour == time(8)


# --- GRAP advisory ------------------------------------------------------------

def test_grap_block_only_when_school_hours_mean_is_severe():
    # Severe morning but Very Poor school-hours mean: no GRAP block.
    hourly = curve({6: 300, 7: 300, 8: 300, 9: 300, 10: 200, 11: 150, 12: 150, 13: 150})
    plan = protocol(hourly, school(), TUE, RULES)
    assert plan.school_hours_band is Band.VERY_POOR
    assert not [a for a in plan.actions if a.action == "grap_advisory"]
    assert [a for a in protocol(flat(300), school(), TUE, RULES).actions
            if a.action == "grap_advisory"]


# --- no-action and errors -----------------------------------------------------

def test_clean_day_is_a_no_action_plan_with_bands_reported():
    plan = protocol(flat(50), S, TUE, RULES)
    assert plan.no_action and plan.header is None
    assert plan.school_hours_band is Band.SATISFACTORY


def test_forecast_must_cover_the_timetable():
    late = school(pe("5A", "14:30", "15:10"))
    with pytest.raises(ValueError, match="no band for hours"):
        protocol(flat(200, hours=range(5, 14)), late, TUE, RULES)


# --- demo school config -------------------------------------------------------

def test_demo_school_loads_and_window_covers_timetable():
    s = load_school(ROOT / "school.yaml")
    assert s.asthma["3B"] == 4
    assert s.hour_window() == range(6, 14)       # 08:00 start - 2h .. 13:50 end
    plan = protocol(flat(200, hours=s.hour_window()), s, TUE, RULES)
    assert {a.action for a in plan.actions} >= {"indoor", "windows_closed"}


def test_school_rejects_unknown_class(tmp_path):
    p = tmp_path / "school.yaml"
    p.write_text("id: x\nname: X\nclasses: {3B: 1}\nweekdays: [0]\nday:\n"
                 "  - {name: PE, who: 9Z, start: '09:00', end: '09:40', outdoor: true}\n")
    with pytest.raises(ValueError, match="unknown class"):
        load_school(p)
