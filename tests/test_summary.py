from datetime import date, datetime, time
from pathlib import Path

import pytest
import yaml

from saans import summary as sm
from saans.bands import Band
from saans.ingest import IST
from saans.rules import load_rules, protocol
from saans.school import load_school

ROOT = Path(__file__).resolve().parent.parent
RULES, _ = load_rules(ROOT / "rules.yaml")
SCHOOL = load_school(ROOT / "school.yaml")
TUE = date(2025, 11, 11)
PLAN = protocol({datetime.combine(TUE, time(h), IST): float(v) for h, v in
                 {5: 320, 6: 320, 7: 320, 8: 320, 9: 320, 10: 300, 11: 280, 12: 150,
                  13: 150, 14: 150}.items()}, SCHOOL, TUE, RULES)
CASES = yaml.safe_load((ROOT / "evals" / "summary_validator_cases.yaml").read_text(encoding="utf-8"))["cases"]


def test_eval_set_has_twenty_cases():
    assert len(CASES) == 20


@pytest.mark.parametrize("case", CASES, ids=[c["text"][:40] or "empty" for c in CASES])
def test_validator_eval(case):
    reasons = sm.validate(case["text"], PLAN)
    if case["ok"]:
        assert reasons == [], reasons
    else:
        assert any(case["reason"] in r for r in reasons), reasons


def test_allowed_bands_cover_actions_school_hours_and_worst():
    assert sm.allowed_bands(PLAN) == {Band.SEVERE, Band.VERY_POOR}


def test_facts_are_plain_text_without_previews():
    f = sm.facts(PLAN, SCHOOL)
    assert "<" not in f and "blockquote" not in f
    assert "Cancelled, outdoor PE" in f and "हिन्दी" not in f


def test_summarize_accepts_a_valid_reply():
    seen = {}

    def model(system, prompt):
        seen["system"], seen["prompt"] = system, prompt
        return "  A Severe morning:   all outdoor activity is cancelled.\n"
    assert sm.summarize(PLAN, SCHOOL, model) == "A Severe morning: all outdoor activity is cancelled."
    assert "Use only facts in the plan" in seen["system"]
    assert "Cancelled, outdoor PE" in seen["prompt"]


def test_summarize_rejects_invalid_reply():
    assert sm.summarize(PLAN, SCHOOL, lambda s, p: "PM2.5 hits 400, stay safe!") is None


def test_summarize_survives_model_errors():
    def broken(system, prompt):
        raise TimeoutError("bedrock timed out")
    assert sm.summarize(PLAN, SCHOOL, broken) is None


def test_no_model_configured_means_no_summary(monkeypatch):
    monkeypatch.delenv("SAANS_BEDROCK_MODEL_ID", raising=False)
    assert sm.strands_model_call() is None
    assert sm.summarize(PLAN, SCHOOL, None) is None


def test_strands_call_is_built_when_configured(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    call = sm.strands_model_call(model_id="test-model-id")
    assert callable(call)       # no network: the Bedrock client is only used when called
