"""Tests for Milestone C1: deterministic scoring, structured results, and the
scaffolded (fail-closed) LLM equivalence judge in src.evaluation.

Plan.md §C1 requirements pinned here:
- numeric canonicalization (commas, signs, decimals, fractions, units,
  task-approved tolerances);
- yes/no canonicalization accepting only unambiguous variants;
- short text = normalized exact match + approved alias list (never substring);
- structured result with method, normalized values, decision, reason;
- judge-required answers are ``unscored``, never incorrect/silently judged;
- the judge refuses to run unless enabled AND C3-approved, routes through
  GenerationClient, validates its reply schema, and records a ``judgment``
  schema-stamped record with prompt/receipt/cache key.
"""

import json
from decimal import Decimal

import pytest

from src import schemas
from src.evaluation import (
    AliasList,
    EvaluationResult,
    JudgeConfig,
    JudgeReply,
    evaluate_answer,
    evaluate_with_llm,
    judge_equivalence,
    load_alias_list,
    load_judge_config,
    normalize_text,
    normalize_yes_no,
    numeric_equivalent,
    parse_numeric,
    short_text_equivalent,
    yes_no_equivalent,
)
from src.generation import GenerationResult


# --------------------------------------------------------------------------- #
# Numeric canonicalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,value",
    [
        ("42", "42"),
        ("56,551", "56551"),
        ("-3", "-3"),
        ("+7", "7"),
        ("3.0", "3"),
        ("0.5", "0.5"),
        (".5", "0.5"),
        ("3/4", "0.75"),
        ("6/3", "2"),
        ("1 1/2", "1.5"),
        ("-2 1/4", "-2.25"),
        ("\\frac{3}{4}", "0.75"),
        ("75%", "0.75"),
        ("75 percent", "0.75"),
        ("$660", "660"),
        ("$9,080", "9080"),
        ("€1.50", "1.5"),
        ("$-3", "-3"),
    ],
)
def test_parse_numeric_forms(text, value):
    parsed = parse_numeric(text)
    assert parsed is not None
    assert parsed.value == Decimal(value)
    assert parsed.unit is None


def test_parse_numeric_simple_units():
    parsed = parse_numeric("42 apples")
    assert parsed is not None and parsed.value == Decimal(42) and parsed.unit == "apples"
    parsed = parse_numeric("3.5 Square Meters")
    assert parsed is not None and parsed.value == Decimal("3.5") and parsed.unit == "square meters"


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "about 12",  # leading prose
        "1e3",  # exponent notation is out of scope
        "12-15",  # range, not a number with unit
        "3/0",  # zero denominator
        "twelve",
        "1/2/3",
        "$$5",  # more than one currency symbol
        "$",  # symbol only
        "5$",  # trailing symbol is not stripped
    ],
)
def test_parse_numeric_rejects_non_numbers(text):
    assert parse_numeric(text) is None


def test_numeric_equivalent_currency_prefixed_answers_match():
    # The full baseline (2026-07-22) showed GSM8K-style answers habitually
    # carry '$'; ground truths are bare numbers. This must be equivalent.
    assert numeric_equivalent("$660", "660").decision == "equivalent"
    assert numeric_equivalent("$9,080", "9080").decision == "equivalent"
    assert numeric_equivalent("$245.00", "245").decision == "equivalent"


def test_numeric_equivalent_exact_and_commas():
    result = numeric_equivalent("56,551", "56551")
    assert result.decision == "equivalent"
    assert result.method == "numeric_exact"
    assert result.normalized_prediction == "56551"
    assert result.normalized_ground_truth == "56551"


def test_numeric_equivalent_fraction_matches_decimal():
    result = numeric_equivalent("3/4", "0.75")
    assert result.decision == "equivalent"


def test_numeric_equivalent_unit_policy():
    # A unit on only one side is ignored (researcher decision 2026-07-22:
    # units never make an answer incorrect).
    assert numeric_equivalent("42 apples", "42").decision == "equivalent"
    assert numeric_equivalent("10 miles", "10").decision == "equivalent"
    assert numeric_equivalent("42", "42 apples").decision == "equivalent"
    assert numeric_equivalent("42 apples", "42 apples").decision == "equivalent"
    # Wrong value with a one-sided unit is still incorrect.
    assert numeric_equivalent("41 miles", "42").decision == "not_equivalent"
    # Conflicting units on BOTH sides are ambiguous -> unscored, not incorrect.
    conflict = numeric_equivalent("42 apples", "42 oranges")
    assert conflict.decision == "unscored"
    assert "conflicting units" in conflict.reason


def test_numeric_equivalent_tolerance_is_explicit():
    exact = numeric_equivalent("3.01", "3.0")
    assert exact.decision == "not_equivalent"
    tolerant = numeric_equivalent("3.01", "3.0", tolerance=Decimal("0.05"))
    assert tolerant.decision == "equivalent"
    assert tolerant.method == "numeric_tolerance"
    with pytest.raises(ValueError, match="non-negative"):
        numeric_equivalent("3", "3", tolerance=Decimal("-1"))


def test_numeric_equivalent_unparseable_prediction_is_not_equivalent():
    result = numeric_equivalent("about 12", "12")
    assert result.decision == "not_equivalent"
    assert result.normalized_prediction is None
    assert result.normalized_ground_truth == "12"


def test_numeric_equivalent_rejects_nonnumeric_ground_truth():
    with pytest.raises(ValueError, match="ground truth"):
        numeric_equivalent("12", "about 12")


# --------------------------------------------------------------------------- #
# Yes/no canonicalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "yes"), ("Yes.", "yes"), ("YES", "yes"), ("y", "yes"),
        ("true", "yes"), ("correct", "yes"),
        ("no", "no"), ("No!", "no"), ("n", "no"),
        ("false", "no"), ("incorrect", "no"),
        ("The answer is yes", None),  # elaborated -> ambiguous
        ("yes, but only if...", None),
        ("probably", None),
        ("maybe", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_yes_no(text, expected):
    assert normalize_yes_no(text) == expected


def test_yes_no_equivalent_decisions():
    assert yes_no_equivalent("Yes.", "yes").decision == "equivalent"
    flipped = yes_no_equivalent("yes", "no")
    assert flipped.decision == "not_equivalent"
    assert flipped.normalized_prediction == "yes"
    assert flipped.normalized_ground_truth == "no"


def test_yes_no_ambiguous_prediction_is_unscored_not_wrong():
    result = yes_no_equivalent("probably yes", "yes")
    assert result.decision == "unscored"
    assert result.is_equivalent is None


def test_yes_no_rejects_ambiguous_ground_truth():
    with pytest.raises(ValueError, match="ground truth"):
        yes_no_equivalent("yes", "probably")


# --------------------------------------------------------------------------- #
# Short text + aliases
# --------------------------------------------------------------------------- #


def test_normalize_text_handles_case_whitespace_punctuation_articles():
    assert normalize_text("  The  New   York City. ") == "new york city"
    assert normalize_text("**Tokyo**") == "tokyo"
    assert normalize_text("An apple") == "apple"
    assert normalize_text("") is None
    assert normalize_text(None) is None


def test_short_text_normalized_exact():
    result = short_text_equivalent("  New   York City. ", "new york city")
    assert result.decision == "equivalent"
    assert result.method == "normalized_text_exact"


def test_short_text_no_substring_equivalence():
    # Containment is NOT equivalence (plan.md §C1).
    result = short_text_equivalent("New York City, USA", "New York City")
    assert result.decision != "equivalent"


def test_short_text_alias_match():
    aliases = AliasList(
        task_id="factual-001",
        ground_truth="New York City",
        aliases=("NYC", "the Big Apple"),
        approved_by="researcher",
        approved_on="2026-07-22",
    )
    result = short_text_equivalent("NYC", "New York City", aliases=aliases)
    assert result.decision == "equivalent"
    assert result.method == "alias_exact"
    assert result.alias_matched == "NYC"
    # An unlisted near-miss is still not equivalent.
    other = short_text_equivalent("New York", "New York City", aliases=aliases)
    assert other.decision != "equivalent"


def test_short_text_numeric_fallback():
    result = short_text_equivalent("56,551", "56551")
    assert result.decision == "equivalent"
    assert result.method == "numeric_fallback"


def test_short_text_short_miss_is_unscored_long_miss_not_equivalent():
    short = short_text_equivalent("NYC", "New York City")
    assert short.decision == "unscored"
    long = short_text_equivalent(
        "the metropolitan area surrounding the harbor on the atlantic coast",
        "new york city",
    )
    assert long.decision == "not_equivalent"


def test_load_alias_list_validates(tmp_path):
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "factual-001": {
                    "ground_truth": "New York City",
                    "aliases": ["NYC"],
                    "approved_by": "researcher",
                    "approved_on": "2026-07-22",
                }
            }
        )
    )
    lists = load_alias_list(path)
    assert lists["factual-001"].aliases == ("NYC",)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(ValueError, match="keyed by task id"):
        load_alias_list(bad)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


def test_evaluate_answer_dispatches_by_format():
    assert evaluate_answer("42", "42", "number").decision == "equivalent"
    assert evaluate_answer("yes", "no", "yes_no").decision == "not_equivalent"
    assert evaluate_answer("NYC", "New York City", "short_text").decision == "unscored"
    with pytest.raises(ValueError, match="answer_format"):
        evaluate_answer("x", "y", "essay")


def test_evaluation_result_is_structured_and_frozen():
    result = evaluate_answer("42", "42", "number")
    assert isinstance(result, EvaluationResult)
    assert result.is_equivalent is True
    assert result.reason
    with pytest.raises(AttributeError):
        result.decision = "not_equivalent"  # frozen dataclass


# --------------------------------------------------------------------------- #
# Judge config + scaffolded judge
# --------------------------------------------------------------------------- #

_TEMPLATE = (
    "Decide whether Answer A and Answer B are equivalent.\n"
    "Answer A: {answer_a}\nAnswer B: {answer_b}\n"
    'Reply with JSON: {"equivalent": bool, "confidence": 0-1, "rationale": str}'
)


def _judge_config(**overrides):
    fields = {
        "judge_id": "answer-equivalence",
        "rubric_version": "1.0.0",
        "prompt_template": _TEMPLATE,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    fields.update(overrides)
    return JudgeConfig(**fields)


def test_judge_config_disabled_by_default_and_not_approved():
    config = _judge_config()
    assert config.enabled is False
    with pytest.raises(ValueError, match="approved"):
        _judge_config(enabled=True)  # enabled without C3 approval must fail


def test_judge_config_enabled_requires_provenance_approval():
    config = _judge_config(
        enabled=True,
        provenance={
            "status": "approved",
            "validation_report": "docs/validation/judge_v1.md",
            "approved_on": "2026-07-22",
        },
    )
    assert config.enabled is True


def test_judge_prompt_is_blinded():
    for banned in ("{model}", "{provider}", "{condition}", "{cue}", "{expected}"):
        with pytest.raises(ValueError, match="must not expose"):
            _judge_config(prompt_template=f"{banned} {{answer_a}} {{answer_b}}")


def test_judge_prompt_requires_placeholders():
    with pytest.raises(ValueError, match="answer_a"):
        _judge_config(prompt_template="no placeholders here")


def test_load_judge_config_validates(tmp_path):
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(_judge_config().model_dump()))
    assert load_judge_config(path).judge_id == "answer-equivalence"
    path.write_text(json.dumps({"judge_id": "x"}))
    with pytest.raises(Exception):
        load_judge_config(path)


class _FakeClient:
    def __init__(self, text):
        self._text = text
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return GenerationResult(
            text=self._text,
            provider="deepseek",
            model="deepseek-v4-flash",
            timestamp="2026-07-22T00:00:00+00:00",
            cache_hit=False,
            cache_key="c" * 64,
            request={},
            raw_response={"id": "resp_1", "usage": {"prompt_tokens": 10}},
        )


def _approved_config():
    return _judge_config(
        enabled=True,
        provenance={"status": "approved", "validation_report": "docs/validation/judge_v1.md"},
    )


def test_judge_disabled_fails_closed():
    with pytest.raises(RuntimeError, match="not enabled"):
        judge_equivalence("NYC", "New York City", config=_judge_config(), client=_FakeClient("{}"))


def test_judge_equivalent_reply(tmp_path):
    client = _FakeClient('{"equivalent": true, "confidence": 0.9, "rationale": "same city"}')
    judgments = tmp_path / "judgments.jsonl"
    result = judge_equivalence(
        "NYC", "New York City", config=_approved_config(), client=client,
        task_id="factual-001", run_id="run-x", judgments_path=judgments,
    )
    assert result.decision == "equivalent"
    assert result.method == "llm_judge"
    assert result.judge == JudgeReply(equivalent=True, confidence=0.9, rationale="same city")
    # Blinding: the prompt contains only the two answers.
    prompt = client.requests[0].messages[0]["content"]
    assert "NYC" in prompt and "New York City" in prompt
    assert "factual-001" not in prompt and "run-x" not in prompt

    record = json.loads(judgments.read_text().strip())
    schemas.check_payload("judgment", record)
    assert record["schema"] == "judgment"
    assert record["decision"] == "equivalent"
    assert record["prompt"] == prompt
    assert record["cache_key"] == "c" * 64
    assert record["receipt"]["id"] == "resp_1"


def test_judge_low_confidence_is_unscored(tmp_path):
    client = _FakeClient('{"equivalent": true, "confidence": 0.2, "rationale": "unsure"}')
    result = judge_equivalence(
        "NYC", "New York City", config=_approved_config(), client=client,
        judgments_path=tmp_path / "j.jsonl",
    )
    assert result.decision == "unscored"
    assert "below threshold" in result.reason


def test_judge_malformed_reply_is_unscored(tmp_path):
    client = _FakeClient("not json at all")
    result = judge_equivalence(
        "NYC", "New York City", config=_approved_config(), client=client,
        judgments_path=tmp_path / "j.jsonl",
    )
    assert result.decision == "unscored"
    assert "schema validation" in result.reason


def test_unvalidated_evaluate_with_llm_still_fails_closed():
    with pytest.raises(RuntimeError, match="not configured"):
        evaluate_with_llm("New York City", "NYC")
