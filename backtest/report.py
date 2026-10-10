"""Static proof page for judges (T13 / design 4A, 12A, 14A).

Every number is computed here from the same code the bot runs (backtest.run,
saans.forecast, saans.rules, saans.messages), so the page cannot drift from the
data. Output: one self-contained HTML file (fonts embedded, inline SVG, no CDN).

Usage: python backtest/report.py [--out docs/index.html]
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.run import run  # noqa: E402
from saans.bands import Band, band  # noqa: E402
from saans.calendar import load_calendar  # noqa: E402
from saans.forecast import Series, forecast  # noqa: E402
from saans.ingest import IST  # noqa: E402
from saans.messages import render_plan  # noqa: E402
from saans.pipeline import load_series_csv  # noqa: E402
from saans.rules import load_rules, protocol  # noqa: E402
from saans.school import load_school  # noqa: E402

ALERT_DAY = date(2025, 11, 10)
TARGET = date(2025, 11, 11)
e = lambda v: html.escape(str(v), quote=True)  # noqa: E731


def lead_time(orders: list[dict]) -> tuple[float, dict]:
    order = next(o for o in orders if o["date"] == TARGET and o.get("not_before"))
    at = datetime.combine(TARGET, time.fromisoformat(order["not_before"]), IST)
    alert = datetime.combine(ALERT_DAY, time(18), IST)
    return (at - alert).total_seconds() / 3600, order


def chart(series: Series, fc: dict[datetime, float], start: datetime, end: datetime,
          width: int, css_class: str, label: str, order_at: datetime | None,
          alert_at: datetime | None) -> str:
    hours = [start + timedelta(hours=k) for k in range(int((end - start).total_seconds() // 3600) + 1)]
    vals = [series.get(h, (None, 0))[0] for h in hours]
    top = max([v for v in vals if v] + [v for h, v in fc.items() if start <= h <= end] + [300])
    ymax = int(-(-top * 1.1 // 50) * 50)
    H, L, R, T, B = 300, 44, 12, 14, 34
    x = lambda h: L + (h - start).total_seconds() / (end - start).total_seconds() * (width - L - R)  # noqa: E731
    y = lambda v: T + (1 - v / ymax) * (H - T - B)  # noqa: E731
    out = [f'<svg class="{css_class}" viewBox="0 0 {width} {H}" role="img" aria-label="{e(label)}">']
    lo = 0
    for upper, b in [(30, Band.GOOD), (60, Band.SATISFACTORY), (90, Band.MODERATE),
                     (120, Band.POOR), (250, Band.VERY_POOR), (ymax, Band.SEVERE)]:
        hi = min(upper, ymax)
        if hi > lo:
            out.append(f'<rect x="{L}" y="{y(hi):.1f}" width="{width - L - R}" height="{y(lo) - y(hi):.1f}" '
                       f'fill="{b.color}" fill-opacity="0.22"/>')
            if y(lo) - y(hi) >= 14:
                out.append(f'<text class="band" x="{width - R - 6}" y="{(y(hi) + y(lo)) / 2 + 4:.1f}" '
                           f'text-anchor="end">{e(b.label)}</text>')
        lo = hi
    for v in range(0, ymax + 1, 100):
        out.append(f'<text class="tick" x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v}</text>')
    day = start.date()
    while datetime.combine(day, time(8), IST) <= end:
        s, t = datetime.combine(day, time(8), IST), datetime.combine(day, time(13, 50), IST)
        if t >= start:
            out.append(f'<rect x="{x(max(s, start)):.1f}" y="{T}" width="{x(min(t, end)) - x(max(s, start)):.1f}" '
                       f'height="{H - T - B}" class="school"/>')
        day += timedelta(days=1)
    for h in hours:
        if h.hour % 6 == 0:
            out.append(f'<text class="tick" x="{x(h):.1f}" y="{H - B + 16}" text-anchor="middle">'
                       f'{h:%d %b %H:%M}</text>')
    # Break the line at missing hours instead of drawing across CPCB data gaps.
    runs, cur = [], []
    for h, v in zip(hours, vals):
        if v is None:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(f"{x(h):.1f},{y(v):.1f}")
    if cur:
        runs.append(cur)
    for run_pts in runs:
        out.append(f'<polyline class="actual" points="{" ".join(run_pts)}"/>')
    gaps = [h for h, v in zip(hours, vals) if v is None]
    if gaps:
        out.append(f'<text class="tick" x="{x(gaps[0]):.1f}" y="{H - B - 6}">no data</text>')
    fpts = " ".join(f"{x(h):.1f},{y(v):.1f}" for h, v in sorted(fc.items()) if start <= h <= end)
    if fpts:
        out.append(f'<polyline class="forecast" points="{fpts}"/>')
    for at, text, anchor in [(alert_at, "Saans alert 18:00", "end"), (order_at, "AQI 425, then the order", "end")]:
        if at and start <= at <= end:
            dx = -6 if anchor == "end" else 6
            out.append(f'<line class="marker" x1="{x(at):.1f}" x2="{x(at):.1f}" y1="{T}" y2="{H - B}"/>'
                       f'<text class="mark" x="{x(at) + dx:.1f}" y="{T + 14}" text-anchor="{anchor}">{e(text)}</text>')
    out.append("</svg>")
    return "".join(out)


def pct(v) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def build(out_path: Path) -> Path:
    series = load_series_csv()
    school = load_school(ROOT / "school.yaml")
    rules, urls = load_rules(ROOT / "rules.yaml")
    orders = yaml.safe_load((ROOT / "officials.yaml").read_text(encoding="utf-8"))["orders"]
    for o in orders:
        o["date"] = o["date"] if isinstance(o["date"], date) else date.fromisoformat(o["date"])
    hours_lead, order = lead_time(orders)

    alert_at = datetime.combine(ALERT_DAY, time(18), IST)
    order_at = datetime.combine(TARGET, time.fromisoformat(order["not_before"]), IST)
    f = forecast(series, alert_at, hours=school.hour_window(), persistence=True)
    plan = protocol(f.hourly, school, TARGET, rules)
    principal = render_plan(plan, school, replay_of=ALERT_DAY)["principal"].split("\n\n<blockquote")[0]
    plain = re.sub(r"<[^>]+>", "", principal)

    bt = run(series, load_calendar(ROOT / "backtest" / "calendar_2025.yaml"))
    sel = bt["selected_method"]
    wide = chart(series, f.hourly, datetime.combine(ALERT_DAY, time(6), IST),
                 datetime.combine(TARGET, time(14), IST), 720, "wide",
                 "Hourly PM2.5, 10 Nov 06:00 to 11 Nov 14:00, rising into Severe on the morning "
                 "of 11 Nov; Saans alerted at 18:00 on 10 Nov.", order_at, alert_at)
    narrow = chart(series, f.hourly, datetime.combine(TARGET, time(0), IST),
                   datetime.combine(TARGET, time(14), IST), 360, "narrow",
                   "Hourly PM2.5 on 11 Nov, Severe through the school morning.", order_at, None)

    def score_rows(threshold: str) -> str:
        rows = []
        for method in ("persistence", "baseline"):
            s = bt["scores"][threshold][method]
            name = method + (" (used)" if method == sel else "")
            rows.append(f"<tr><td>{e(name)}</td><td>{pct(s['hit_rate'])}</td>"
                        f"<td>{pct(s['false_alarm_ratio'])}</td><td>{pct(s['base_rate'])}</td>"
                        f"<td>{s['tp']}/{s['fp']}/{s['fn']}/{s['tn']}</td></tr>")
        return "".join(rows)

    from backtest.metrics import DayResult, score
    days = [DayResult(date.fromisoformat(d["target"]), d["level_today"], d["predicted"], d["actual"])
            for d in bt["days"][sel]]
    severe = score(sel, days, Band.SEVERE)

    rule_rows = "".join(
        f"<tr><td><code>{e(r.id)}</code></td><td>{e(r.trigger.label)}+</td><td>{e(r.applies_to)}</td>"
        f"<td>{e(r.action)}</td><td>{e(r.kind)}</td><td>"
        + ", ".join(f'<a href="{e(urls[s])}">{e(s)}</a>' for s in r.sources) + "</td></tr>"
        for r in rules)
    order_rows = "".join(
        f"<tr><td>{o['date']:%d %b %Y}</td><td>{e(o['stage'])}</td><td>{e(o['action'])}</td>"
        f"<td>{e(o.get('not_before') or '—')}</td><td><a href=\"{e(o['source'])}\">source</a></td></tr>"
        for o in orders)

    fonts = {n: base64.b64encode((ROOT / "backtest" / "fonts" / f"{n}.woff2").read_bytes()).decode()
             for n in ("IBMPlexSans-Regular", "IBMPlexSans-SemiBold", "IBMPlexMono-Regular")}
    page = TEMPLATE.format(
        font_regular=fonts["IBMPlexSans-Regular"], font_semibold=fonts["IBMPlexSans-SemiBold"],
        font_mono=fonts["IBMPlexMono-Regular"],
        hours=int(hours_lead), forecast_band=e(band(f.level).label), forecast_level=round(f.level),
        actual=round(bt_actual(bt, sel)), wide=wide, narrow=narrow, plan=e(plain),
        n=bt["scored_days"], window=f"{bt['window'][0]} to {bt['window'][1]}",
        method=e(sel), vp_rows=score_rows("Very Poor"), poor_rows=score_rows("Poor"),
        sev_hit=pct(severe.hit_rate), sev_far=pct(severe.false_alarm_ratio),
        sev_base=pct(severe.base_rate),
        gate_b=pct(bt["gate_b"]["share_actual"]), gate_b_n=bt["gate_b"]["days_actual"],
        rule_rows=rule_rows, order_rows=order_rows,
        reported_by=e(order.get("reported_by") or "later that day"),
        generated=datetime.now(IST).strftime("%d %b %Y %H:%M IST"),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


def bt_actual(bt: dict, method: str) -> float:
    return next(d["actual"] for d in bt["days"][method] if d["target"] == TARGET.isoformat())


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saans · evening-before air plans for Delhi schools</title>
<style>
@font-face {{font-family: Plex; font-weight: 400; src: url(data:font/woff2;base64,{font_regular}) format("woff2");}}
@font-face {{font-family: Plex; font-weight: 600; src: url(data:font/woff2;base64,{font_semibold}) format("woff2");}}
@font-face {{font-family: PlexMono; src: url(data:font/woff2;base64,{font_mono}) format("woff2");}}
:root {{ --ink: #1a1a1a; --muted: #555; --rule: #d9d9d9; --paper: #fff; --focus: #1a1a1a; --soft: #e8e8e8; }}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; background: var(--paper); color: var(--ink); font: 400 17px/1.55 Plex, sans-serif; }}
::selection {{ background: var(--soft); color: var(--ink); }}
main {{ max-width: 760px; margin: 0 auto; padding: 40px 20px 64px; }}
header p.kicker {{ margin: 0 0 8px; font-weight: 600; }}
h1 {{ font-size: 2rem; line-height: 1.2; margin: 0 0 12px; font-weight: 600; }}
h2 {{ font-size: 1.25rem; margin: 48px 0 8px; font-weight: 600; }}
p {{ margin: 0 0 14px; }}
.lede {{ font-size: 1.1rem; }}
.num, td, code, pre, .tick, .mark, .band {{ font-family: PlexMono, monospace; font-variant-numeric: tabular-nums; }}
figure {{ margin: 24px 0; }}
figcaption {{ color: var(--muted); font-size: 0.9rem; margin-top: 6px; }}
svg {{ width: 100%; height: auto; display: block; }}
svg .narrow, svg.narrow {{ display: none; }}
.tick {{ font-size: 11px; fill: var(--muted); }}
.band {{ font-size: 11px; fill: var(--ink); }}
.mark {{ font-size: 12px; fill: var(--ink); }}
.actual {{ fill: none; stroke: var(--ink); stroke-width: 2; }}
.forecast {{ fill: none; stroke: var(--ink); stroke-width: 2; stroke-dasharray: 5 4; }}
.marker {{ stroke: var(--ink); stroke-width: 1.5; }}
.school {{ fill: var(--ink); fill-opacity: 0.06; }}
pre {{ white-space: pre-wrap; background: #f6f6f6; border: 1px solid var(--rule); padding: 14px 16px;
       font-size: 0.85rem; line-height: 1.5; margin: 0; }}
.scroll {{ overflow-x: auto; }}
td code {{ white-space: nowrap; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--rule); vertical-align: top; }}
th {{ font-weight: 600; }}
th:first-child, td:first-child {{ position: sticky; left: 0; background: var(--paper); }}
a {{ color: #0b57d0; text-underline-offset: 3px; }}
a:visited {{ color: #6a3fb5; }}
a:focus-visible {{ outline: 2px solid var(--focus); outline-offset: 2px; }}
ul {{ padding-left: 20px; }}
footer {{ margin-top: 48px; color: var(--muted); font-size: 0.85rem; }}
@media (max-width: 600px) {{
  body {{ font-size: 16px; }}
  h1 {{ font-size: 1.6rem; }}
  svg.wide {{ display: none; }}
  svg.narrow {{ display: block; }}
}}
</style>
</head>
<body>
<main>
<header>
<p class="kicker">Saans · evening-before air plans for Delhi schools</p>
<h1>Saans warned the evening before the official order.</h1>
<p class="lede">At 18:00 on Mon 10 Nov 2025, using only data published by 16:00, Saans forecast a
<strong>{forecast_band}</strong> school day for Tue 11 Nov (PM2.5 about <span class="num">{forecast_level}</span> µg/m³;
it turned out <span class="num">{actual}</span>). Delhi's AQI read 425 at 09:00 on 11 Nov. GRAP Stage III and the
DoE order moving classes up to V to hybrid came after that, reported by {reported_by}. That is at least
<strong class="num">{hours} hours</strong> of warning, and the official order arrived after the school day had begun.</p>
</header>

<figure>
{wide}{narrow}
<figcaption>Solid line: hourly PM2.5, mean of 6 CPCB stations. Dashed: the forecast Saans made at 18:00 on
10 Nov. Grey: school hours (08:00-13:50). Band colours are CPCB's PM2.5 bands; this is a PM2.5 estimate,
not the official AQI.</figcaption>
</figure>

<h2>The message the principal got at 18:00</h2>
<pre>{plan}</pre>
<p>Approve & send posts a Hindi-first notice to parents and a class-by-class list to staff. Parents never see
class names or the GRAP guess.</p>

<h2>How often it was right</h2>
<p>{n} school days, {window}. Each forecast was made at 18:00 the evening before, using only data published
by 16:00. Method in use: <strong>{method}</strong>, chosen by a rule fixed before the results were seen.</p>
<div class="scroll"><table>
<thead><tr><th>Tomorrow is Very Poor or worse</th><th>Caught</th><th>False alarms</th><th>How often it happened</th><th>TP/FP/FN/TN</th></tr></thead>
<tbody>{vp_rows}</tbody></table></div>
<div class="scroll"><table>
<thead><tr><th>Tomorrow is Poor or worse</th><th>Caught</th><th>False alarms</th><th>How often it happened</th><th>TP/FP/FN/TN</th></tr></thead>
<tbody>{poor_rows}</tbody></table></div>
<p>Where it is weak: Severe days specifically were caught {sev_hit} of the time with {sev_far} false alarms
(Severe happened on {sev_base} of days). That is why the "likely GRAP Stage III" note goes only to the
principal and staff, never to parents. Persistence also never predicts a change of band; its value is the
evening lead time on the many days the air stays bad.</p>
<p>We also tested moving PE to cleaner afternoon hours. The afternoon was a full band cleaner on only
{gate_b} of {gate_b_n} bad days, below the 50% bar set in advance, so that feature was dropped.</p>

<h2>The rules and their sources</h2>
<p>Rules make every decision; the AI only writes an optional one-sentence summary, checked against the plan.</p>
<div class="scroll"><table>
<thead><tr><th>Rule</th><th>From</th><th>Applies to</th><th>Action</th><th>Kind</th><th>Sources</th></tr></thead>
<tbody>{rule_rows}</tbody></table></div>

<h2>Official orders used for comparison</h2>
<div class="scroll"><table>
<thead><tr><th>Date</th><th>Order</th><th>What</th><th>Not before</th><th>Link</th></tr></thead>
<tbody>{order_rows}</tbody></table></div>

<h2>Data notes</h2>
<ul>
<li>CPCB hourly PM2.5 via the OpenAQ API, mean of 6 Delhi stations (Anand Vihar, ITO, RK Puram, Punjabi Bagh,
Mandir Marg, Lodhi Road).</li>
<li>CPCB data has network-wide 3-4 hour gaps. When a day has 3-5 of its 6 school hours, its level is
estimated from the hours present using the typical daily curve; fewer than 3 means no forecast.</li>
<li>"Saans air band" maps PM2.5 onto CPCB's 24-hour PM2.5 bands. It is not the official AQI.</li>
<li>Only data published by 16:00 is used for an 18:00 forecast. Archived values may have been revised since.</li>
</ul>
<footer>Generated {generated} by backtest/report.py from the same code the bot runs. Built with the
Strands Agents SDK.</footer>
</main>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "index.html")
    path = build(ap.parse_args().out)
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
