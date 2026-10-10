from datetime import date, datetime, time
from pathlib import Path

import pytest

from saans import messages as m
from saans.bands import Band
from saans.ingest import IST
from saans.rules import load_rules, protocol
from saans.school import Activity, School, load_school
from saans.templates import T

ROOT = Path(__file__).resolve().parent.parent
RULES, _ = load_rules(ROOT / "rules.yaml")
SCHOOL = load_school(ROOT / "school.yaml")
TUE = date(2025, 11, 11)
MON = date(2025, 11, 10)


def flat(value, hours=range(5, 15), day=TUE):
    return {datetime.combine(day, time(h), IST): float(value) for h in hours}


def curve(values, day=TUE):
    return {datetime.combine(day, time(h), IST): float(v) for h, v in values.items()}


SEVERE = protocol(flat(300), SCHOOL, TUE, RULES)
# Severe morning easing to Very Poor by 13:00, like the real Nov 11 replay
MIXED = protocol(curve({5: 320, 6: 320, 7: 320, 8: 320, 9: 320, 10: 300, 11: 280, 12: 150,
                        13: 150, 14: 150}), SCHOOL, TUE, RULES)     # school-hours mean 253: Severe
POOR = protocol(flat(110), SCHOOL, TUE, RULES)
CLEAN = protocol(flat(70), SCHOOL, TUE, RULES)


def render(plan=SEVERE, **kw):
    return m.render_plan(plan, SCHOOL, **kw)


def test_dates_and_no_relative_words():
    assert m.fmt_date(TUE, "en") == "Tue 11 Nov"
    assert m.fmt_date(TUE, "hi") == "मंगल 11 नवंबर"
    assert m.fmt_date(MON, "en", year=True) == "Mon 10 Nov 2025"
    for text in render().values():
        assert "Tue 11 Nov" in text and "मंगल 11 नवंबर" in text


def test_every_rendered_text_passes_the_tone_lint():
    texts = list(render(MIXED).values()) + list(render(POOR).values()) + [
        m.render_no_action(CLEAN), m.render_unavailable(TUE), m.render_reminder(TUE),
        m.stamp("approved", time(18, 42), {"parents": "POSTED", "staff": "FAILED"}),
        m.stamp("skipped", time(18, 40)), m.stamp("expired"), m.stamp("replaced"),
        m.system("help"), m.system("not_registered")]
    for text in texts:
        assert m.lint(text) == [], text


def test_principal_is_decision_first_and_grouped():
    p = render(MIXED)["principal"]
    lines = p.splitlines()
    assert lines[0].startswith("<b>Tue 11 Nov · 🟤 Severe")
    assert "Cancelled, outdoor PE: 3B 08:20, 7B 09:00, 9A 09:40, 5A 11:40." in p
    assert "PE indoors: 3B 13:10 in the hall (🔴 Very Poor)." in p
    assert "Recess: indoors, no outdoor play." in p
    assert "Prepare: likely GRAP Stage III" in p
    assert p.count("<blockquote expandable>") == 3


def test_recess_and_assembly_are_never_cancelled_wording():
    p = render()["principal"]
    cancel_line = next(l for l in p.splitlines() if l.startswith("Cancelled"))
    assert "Recess" not in cancel_line and "Assembly" not in cancel_line
    assert "Assembly: in classrooms over the PA." in p


def test_parents_get_no_classes_no_grap_and_the_conditional_line():
    parents = render(MIXED)["parents"]
    for cls in ("3B", "5A", "7B", "9A"):
        assert cls not in parents
    for word in ("GRAP", "hybrid", "हाइब्रिड", "School is open"):
        assert word not in parents
    assert "school runs as usual unless the school announces a change" in parents
    assert "स्कूल सामान्य रूप से चलेगा" in parents
    assert parents.index("स्कूल") < parents.index("school runs")      # Hindi first (primary)


def test_poor_day_parent_notice_does_not_hint_at_asthma_classes():
    poor = render(POOR)
    assert "3B" in poor["staff"]                      # only the asthma class moved indoors
    assert "3B" not in poor["parents"]
    assert "PE will not be held outdoors." in poor["parents"]


def test_staff_list_whole_school_first_then_sorted_classes():
    staff = render(MIXED)["staff"]
    en = staff.split(m.DIVIDER)[1]
    lines = [l for l in en.splitlines() if l]
    assert lines[0].startswith("<b>Tue 11 Nov")
    assert lines[1].startswith("Whole school: assembly in classrooms over the PA")
    class_lines = [l.split(":")[0] for l in lines if l[:2] in ("3B", "5A", "7B", "9A")]
    assert class_lines == ["3B", "5A", "7B", "9A"]
    assert "3B: 08:20 PE, no PE (stay in room) · 13:10 PE in the hall" in en
    assert "Prepare: likely GRAP Stage III" in en


def test_replay_prefix_on_every_surface():
    for text in render(replay_of=MON).values():
        assert "REPLAY · using data from the evening of Mon 10 Nov 2025" in text
        assert "रीप्ले · सोम 10 नवंबर 2025 की शाम के डेटा से" in text
    assert all("REPLAY" not in t for t in render().values())


def test_dynamic_values_are_html_escaped():
    evil = School("x", "X", {"<b>7&B": 1}, frozenset(range(6)),
                  (Activity("PE", "<b>7&B", time(9), time(9, 40), True),),
                  indoor_venue={"PE": {"en": "<script>", "hi": "हॉल"}})
    plan = protocol(flat(110), evil, TUE, RULES)
    tx = m.render_plan(plan, evil, summary="<i>hi</i> & <script>alert(1)</script>")
    p = tx["principal"]
    assert "&lt;b&gt;7&amp;B" in p and "<b>7&B" not in p
    assert "&lt;script&gt;" in p and "<script>" not in p


def test_llm_summary_only_in_english_principal_text():
    tx = render(summary="Very bad morning; afternoon slightly better.")
    main = tx["principal"].split("<blockquote")[0]
    assert "Very bad morning" in main
    assert "Very bad morning" not in tx["parents"] and "Very bad morning" not in tx["staff"]


def school_with(n):
    classes = {f"C{i:03d}": 1 for i in range(n)}
    acts = tuple(Activity("PE", c, time(8 + i % 5, 0), time(8 + i % 5, 40), True)
                 for i, c in enumerate(classes))
    return School(f"s{n}", "S", classes, frozenset(range(6)), acts)


def test_length_guard_falls_back_then_raises():
    big = school_with(96)
    tx = m.render_plan(protocol(flat(110), big, TUE, RULES), big)
    assert all(len(t) <= m.LIMIT for t in tx.values())
    assert m.DIVIDER not in tx["staff"]              # staff fell back to the primary language
    assert tx["principal"].count("<blockquote") < 3   # least important previews dropped
    huge = school_with(400)
    with pytest.raises(m.MessageTooLong):
        m.render_plan(protocol(flat(110), huge, TUE, RULES), huge)
    small = school_with(10)
    tx = m.render_plan(protocol(flat(110), small, TUE, RULES), small)
    assert m.DIVIDER in tx["staff"] and tx["principal"].count("<blockquote") == 3


def test_no_action_plan_is_rendered_separately():
    with pytest.raises(ValueError):
        render(CLEAN)
    text = m.render_no_action(CLEAN)
    assert "Tue 11 Nov: no changes needed" in text and "Nothing will be sent." in text
    assert "कुछ नहीं भेजा जाएगा" in text


def test_system_messages():
    assert "SAMEER" in m.render_unavailable(TUE)
    assert "expires at 07:00" in m.render_reminder(TUE)
    fwd = m.render_forward("staff", "STAFF TEXT")
    assert "staff group" in fwd and fwd.endswith("STAFF TEXT")
    s = m.stamp("approved", time(18, 42), {"parents": "POSTED", "staff": "FAILED"})
    assert "Approved 18:42 · Parents: sent · Staff: could not post" in s


def test_toasts_fit_telegram_limit():
    for key in [k for k in T["en"] if k.startswith("toast_")] + ["not_registered"]:
        assert len(m.toast(key)) <= 200


def test_templates_have_the_same_keys_in_both_languages():
    assert set(T["en"]) - {"preview_hi", "preview_parents", "preview_staff",
                           "btn_approve", "btn_skip"} == set(T["hi"])
