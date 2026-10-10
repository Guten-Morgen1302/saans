"""Render plans and system states into Telegram HTML (design 1A-3A, 8A-17A; eng R17).

Every dynamic value is HTML-escaped before it reaches a template, so class
names, venues and the LLM summary can never inject markup (design 13A).

    Plan ──▶ principal: EN plan + <blockquote expandable> HI / parent / staff previews
         ├─▶ parents:   primary-language block, divider, other language (no classes, no GRAP)
         └─▶ staff:     primary-language block, divider, other language (whole school, then classes)
"""

from __future__ import annotations

import html
from datetime import date, time

from saans.bands import SAANS_BAND, Band
from saans.rules import Action, Plan
from saans.school import School
from saans.templates import BANNED, T

LIMIT = 4096
DIVIDER = "──────────"


class MessageTooLong(ValueError):
    pass


def _e(v) -> str:
    return html.escape(str(v), quote=False)


def t(lang: str, key: str, **kw) -> str:
    return T[lang][key].format(**{k: _e(v) for k, v in kw.items()})


def fmt_date(d: date, lang: str, year: bool = False) -> str:
    tt = T[lang]
    return tt["date_year" if year else "date"].format(
        wd=tt["weekdays"][d.weekday()], d=d.day, mon=tt["months"][d.month - 1], y=d.year)


def _hm(x: time) -> str:
    return x.strftime("%H:%M")


def _other(lang: str) -> str:
    return "en" if lang == "hi" else "hi"


def _replay_line(lang: str, replay_of: date | None) -> list[str]:
    return [t(lang, "replay", date=fmt_date(replay_of, lang, year=True))] if replay_of else []


def _header(plan: Plan, lang: str) -> str:
    day = fmt_date(plan.target, lang)
    if not plan.header:
        return T[lang]["header_plain"].format(date=_e(day))
    start, end, b = plan.header
    return T[lang]["header"].format(date=_e(day), dot=b.dot, band=_e(b.name_in(lang)),
                                    start=_hm(start), end=_hm(end))


def _split(plan: Plan):
    pe_cancel = [a for a in plan.actions if a.activity == "PE" and a.action == "cancel"]
    pe_indoor = [a for a in plan.actions if a.activity == "PE" and a.action == "indoor"]
    assembly = any(a.activity == "Assembly" for a in plan.actions)
    recess = any(a.activity == "Recess" for a in plan.actions)
    windows = next((a for a in plan.actions if a.action == "windows_closed"), None)
    grap = any(a.action == "grap_advisory" for a in plan.actions)
    return pe_cancel, pe_indoor, assembly, recess, windows, grap


def _principal_block(plan: Plan, school: School, lang: str, replay_of, summary) -> str:
    pe_cancel, pe_indoor, assembly, recess, windows, grap = _split(plan)
    lines = _replay_line(lang, replay_of) + [
        _header(plan, lang),
        t(lang, "band_note", saans_band=SAANS_BAND[lang]),
        "",
    ]
    if pe_cancel:
        items = ", ".join(f"{a.who} {_hm(a.start)}" for a in pe_cancel)
        lines.append(t(lang, "pe_cancel", items=items))
    if pe_indoor:
        items = "; ".join(
            T[lang]["pe_indoor_item"].format(who=_e(a.who), time=_hm(a.start),
                                             venue=_e(school.venue("PE", lang)),
                                             dot=a.band.dot, band=_e(a.band.name_in(lang)))
            for a in pe_indoor)
        lines.append(T[lang]["pe_indoor"].format(items=items))
    if assembly:
        lines.append(T[lang]["assembly"])
    if recess:
        lines.append(T[lang]["recess"])
    if windows:
        lines.append(t(lang, "windows", start=_hm(windows.start), end=_hm(windows.end)))
    if grap:
        lines += ["", T[lang]["grap"]]
    if summary and lang == "en":
        lines += ["", f"<i>{_e(summary)}</i>"]
    return "\n".join(lines)


def _parent_block(plan: Plan, lang: str, replay_of) -> str:
    pe_cancel, pe_indoor, assembly, recess, windows, _ = _split(plan)
    b = plan.worst_band
    lines = _replay_line(lang, replay_of) + [
        t(lang, "parent_open", date=fmt_date(plan.target, lang)),
        t(lang, "parent_band", dot=b.dot, band=b.name_in(lang), saans_band=SAANS_BAND[lang]),
    ]
    if pe_cancel or pe_indoor:
        lines.append(T[lang]["parent_pe"])
    if assembly:
        lines.append(T[lang]["parent_assembly"])
    if recess:
        lines.append(T[lang]["parent_recess"])
    if windows:
        lines.append(t(lang, "parent_windows", start=_hm(windows.start), end=_hm(windows.end)))
    lines += ["", t(lang, "parent_note", saans_band=SAANS_BAND[lang])]
    return "\n".join(lines)


def _staff_block(plan: Plan, school: School, lang: str, replay_of) -> str:
    pe_cancel, pe_indoor, assembly, recess, windows, grap = _split(plan)
    whole = []
    if assembly:
        whole.append(T[lang]["staff_assembly"])
    if recess:
        whole.append(T[lang]["staff_recess"])
    if windows:
        whole.append(t(lang, "staff_windows", start=_hm(windows.start), end=_hm(windows.end)))
    lines = _replay_line(lang, replay_of) + [_header(plan, lang)]
    if whole:
        lines.append(T[lang]["staff_whole"].format(items=" · ".join(whole)))
    if grap:
        lines.append(T[lang]["grap"])
    per_class: dict[str, list[tuple[time, str]]] = {}
    for a in pe_cancel:
        per_class.setdefault(a.who, []).append((a.start, t(lang, "staff_pe_cancel", time=_hm(a.start))))
    for a in pe_indoor:
        per_class.setdefault(a.who, []).append(
            (a.start, t(lang, "staff_pe_indoor", time=_hm(a.start), venue=school.venue("PE", lang))))
    for who in sorted(per_class):
        items = " · ".join(text for _, text in sorted(per_class[who]))
        lines.append(T[lang]["staff_class"].format(who=_e(who), items=items))
    return "\n".join(lines)


def _bilingual(primary: str, make) -> list[str]:
    return [make(primary), make(_other(primary))]


def _expandable(title: str, body: str) -> str:
    return f"<blockquote expandable><b>{_e(title)}</b>\n{body}</blockquote>"


def render_plan(plan: Plan, school: School, replay_of: date | None = None,
                summary: str | None = None) -> dict[str, str]:
    """Texts for an actionable plan: {'principal', 'parents', 'staff'}."""
    if plan.no_action:
        raise ValueError("no-action plans are rendered with render_no_action")
    lang = school.primary_language
    parents_parts = _bilingual(lang, lambda l: _parent_block(plan, l, replay_of))
    staff_parts = _bilingual(lang, lambda l: _staff_block(plan, school, l, replay_of))
    parents = _fit_bilingual(parents_parts)
    staff = _fit_bilingual(staff_parts)

    main = _principal_block(plan, school, "en", replay_of, summary)
    extras = [
        _expandable(T["en"]["preview_hi"], _principal_block(plan, school, "hi", replay_of, None)),
        _expandable(T["en"]["preview_parents"], parents),
        _expandable(T["en"]["preview_staff"], staff),
    ]
    # 4096 guard (design 13A): drop previews from the least to the most important.
    for keep in (3, 2, 1, 0):
        text = "\n\n".join([main] + extras[:keep])
        if len(text) <= LIMIT:
            return {"principal": text, "parents": parents, "staff": staff}
    raise MessageTooLong(f"principal plan is {len(main)} chars even without previews")


def _fit_bilingual(parts: list[str]) -> str:
    both = f"\n\n{DIVIDER}\n\n".join(parts)
    if len(both) <= LIMIT:
        return both
    if len(parts[0]) <= LIMIT:
        return parts[0]
    raise MessageTooLong(f"message is {len(parts[0])} chars in one language")


def buttons(approve_data: str, skip_data: str) -> list[tuple[str, str]]:
    return [(T["en"]["btn_approve"], approve_data), (T["en"]["btn_skip"], skip_data)]


def _both(key: str, **kw) -> str:
    return "\n".join(t(lang, key, **kw) for lang in ("en", "hi"))


def _both_dated(key: str, day: date, **kw) -> str:
    return "\n".join(t(lang, key, date=fmt_date(day, lang), **kw) for lang in ("en", "hi"))


def render_no_action(plan: Plan) -> str:
    out = []
    for lang in ("en", "hi"):
        out.append(t(lang, "no_action", date=fmt_date(plan.target, lang),
                     dot=plan.school_hours_band.dot, band=plan.school_hours_band.name_in(lang),
                     wdot=plan.worst_band.dot, wband=plan.worst_band.name_in(lang),
                     hour=_hm(plan.worst_hour)))
    return "\n".join(out)


def render_unavailable(target: date) -> str:
    return _both_dated("unavailable", target)


def render_reminder(target: date) -> str:
    return _both_dated("reminder", target)


def render_forward(channel: str, text: str) -> str:
    aud = {"parents": "aud_parents", "staff": "aud_staff"}[channel]
    head = "\n".join(t(lang, "forward", audience=T[lang][aud]) for lang in ("en", "hi"))
    return f"{head}\n\n{text}"


def system(key: str) -> str:
    """Bilingual system line: not_registered, help."""
    return _both(key)


def toast(key: str) -> str:
    return f"{T['en'][key]} / {T['hi'][key]}"


def stamp(kind: str, at: time | None = None, delivery: dict[str, str] | None = None) -> str:
    """Outcome line appended to the principal's plan (design 5A)."""
    lines = []
    for lang in ("en", "hi"):
        if kind == "approved":
            status = {c: T[lang]["sent" if s == "POSTED" else "not_sent"]
                      for c, s in (delivery or {}).items()}
            line = t(lang, "stamp_approved", time=_hm(at), parents=status.get("parents", "-"),
                     staff=status.get("staff", "-"))
        elif kind == "skipped":
            line = t(lang, "stamp_skipped", time=_hm(at))
        else:
            line = T[lang][f"stamp_{kind}"]
        lines.append(f"<i>{line}</i>")
    return "\n".join(lines)


def with_stamp(principal_text: str, stamp_text: str) -> str:
    return f"{principal_text}\n\n{stamp_text}"


def lint(text: str) -> list[str]:
    """Banned phrases present in a rendered message (design 10A)."""
    low = text.lower()
    return [p for p in BANNED if p.lower() in low]
