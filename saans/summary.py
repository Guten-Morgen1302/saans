"""Optional 2-sentence English summary for the principal (Strands on Bedrock).

The LLM never decides anything: it only restates the plan, and its output is
validated against actions[] before use (eng R9, design 10A). On any rejection,
model error, or missing model config, no summary is shown; the plan's header
line already states the day, band and window.

Validator rejects a summary that:
- is empty, has more than 2 sentences, or is longer than 280 characters
- mentions a time (HH:MM) or class (e.g. 7B) not in the plan
- contains any other number (no PM2.5 values, periods, percentages)
- names a band outside: bands in actions[] + school-hours band + worst band
- uses GRAP / order / official / safe / fine / normal, or fails the tone lint
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable

from saans import messages
from saans.bands import Band
from saans.rules import Plan

log = logging.getLogger(__name__)

MAX_CHARS = 280
BANNED_WORDS = ["grap", "order", "official", "safe", "fine", "normal"]
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
CLASS_RE = re.compile(r"\b\d{1,2}[A-Z]\b")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Longest names first so "Very Poor" is not also counted as "Poor".
BAND_NAMES = sorted(((b.label.lower(), b) for b in Band), key=lambda x: -len(x[0]))

SYSTEM_PROMPT = (
    "You write one or two short plain sentences for a school principal, summarising "
    "tomorrow's air plan that is given to you. Use only facts in the plan. Do not add "
    "numbers, times, class names, advice, reassurance, or any mention of government "
    "orders. No exclamation marks. Reply with the sentences only."
)

ModelCall = Callable[[str, str], str]   # (system_prompt, user_prompt) -> text


def allowed_bands(plan: Plan) -> set[Band]:
    return {a.band for a in plan.actions} | {plan.school_hours_band, plan.worst_band}


def _allowed_times(plan: Plan) -> set[str]:
    times = {a.start.strftime("%H:%M") for a in plan.actions} | \
            {a.end.strftime("%H:%M") for a in plan.actions}
    if plan.header:
        times |= {plan.header[0].strftime("%H:%M"), plan.header[1].strftime("%H:%M")}
    return times


def validate(summary: str, plan: Plan) -> list[str]:
    """Reasons to reject; empty list means the summary may be shown."""
    reasons = []
    text = (summary or "").strip()
    if not text:
        return ["empty"]
    if len(text) > MAX_CHARS:
        reasons.append("too long")
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    if len(sentences) > 2:
        reasons.append("more than 2 sentences")

    times = {m.group(0).zfill(5) for m in TIME_RE.finditer(text)}
    bad_times = times - _allowed_times(plan)
    if bad_times:
        reasons.append(f"times not in plan: {sorted(bad_times)}")
    classes = set(CLASS_RE.findall(text))
    bad_classes = classes - {a.who for a in plan.actions}
    if bad_classes:
        reasons.append(f"classes not in plan: {sorted(bad_classes)}")
    rest = CLASS_RE.sub(" ", TIME_RE.sub(" ", text))
    if NUMBER_RE.search(rest):
        reasons.append("contains a number")

    low = text.lower()
    remaining = low
    named = set()
    for name, b in BAND_NAMES:
        if re.search(rf"\b{name}\b", remaining):
            named.add(b)
            remaining = re.sub(rf"\b{name}\b", " ", remaining)
    bad_bands = named - allowed_bands(plan)
    if bad_bands:
        reasons.append(f"bands not in plan: {sorted(b.label for b in bad_bands)}")
    for word in BANNED_WORDS:
        if re.search(rf"\b{word}\w*\b", low):
            reasons.append(f"banned word: {word}")
    reasons += [f"tone: {p}" for p in messages.lint(text)]
    return reasons


def facts(plan: Plan, school) -> str:
    """The plan as plain English facts: the only input the model sees."""
    html_text = messages.render_plan(plan, school)["principal"].split("\n\n<blockquote")[0]
    return re.sub(r"<[^>]+>", "", html_text)


def summarize(plan: Plan, school, call_model: ModelCall | None) -> str | None:
    if call_model is None or plan.no_action:
        return None
    try:
        text = " ".join(call_model(SYSTEM_PROMPT, facts(plan, school)).split())
    except Exception as e:  # noqa: BLE001 - any model/transport failure means "no summary"
        log.warning("summary model call failed: %s: %s", type(e).__name__, e)
        return None
    reasons = validate(text, plan)
    if reasons:
        log.warning("summary rejected (%s): %r", "; ".join(reasons), text)
        return None
    return text


def strands_model_call(model_id: str | None = None, region: str | None = None) -> ModelCall | None:
    """Strands Agent on Bedrock, or None when no model is configured."""
    model_id = model_id or os.environ.get("SAANS_BEDROCK_MODEL_ID")
    if not model_id:
        log.info("SAANS_BEDROCK_MODEL_ID not set; summaries disabled")
        return None
    from strands import Agent
    from strands.models import BedrockModel

    model = BedrockModel(model_id=model_id, region_name=region or os.environ.get("AWS_REGION"),
                         max_tokens=200, temperature=0.2)

    def call(system_prompt: str, prompt: str) -> str:
        agent = Agent(model=model, system_prompt=system_prompt, callback_handler=None)
        return str(agent(prompt))

    return call
