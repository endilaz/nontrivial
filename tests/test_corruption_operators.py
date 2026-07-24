"""Tests for E3 semantic corruption operators + dispatch.

Covers: math unicode-aware numeric edits + deterministic chain recompute,
logic entity swaps/relation negations (implied answers NOT defined — 2026-07-24
deviation), factual own-context distractors (D-E1e), ethics fact edits and
polarity flips (D-E1d), fail-closed unapplied behavior (spec §7.4), and the
spec §4.1 position-adjustment rule.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.corruption import build_records
from src.corruption_dispatch import (
    TaskContext,
    apply_semantic_edits,
)
from src.corruption_operators import (
    ETHICS_FACT_EDITS,
    ethics_reversal_edit,
    ethics_subtle_edit,
    extract_logic_categories,
    factual_reversal_edit,
    factual_subtle_edit,
    logic_reversal_edit,
    logic_subtle_edit,
    math_implied_answer,
    math_reversal_edit,
    math_subtle_edit,
    _format_like,
    _parse_number,
)
from src.parsing import parse_cot

# ---------------------------------------------------------------------------
# Math operators
# ---------------------------------------------------------------------------


def test_math_subtle_edits_numeric_tail():
    edit = math_subtle_edit("Total sold = 60 + 10 + 20 + 44 + 66 = 200")
    assert edit is not None
    assert edit.edit_kind == "numeric_tail_shift"
    assert "= 200" not in edit.corrupted_step
    assert "60 + 10 + 20 + 44 + 66" in edit.corrupted_step  # givens untouched


def test_math_subtle_unicode_operators_and_units():
    edit = math_subtle_edit("Total hours: $550 ÷ $10 = 55 hours")
    assert edit is not None
    assert "= 55" not in edit.corrupted_step
    assert "hours" in edit.corrupted_step  # unit tail preserved
    edit = math_subtle_edit("Distance = speed × time = 40 × 0.25 = 10 miles.")
    assert edit is not None
    # Only the LAST numeric tail changes.
    assert "40 × 0.25" in edit.corrupted_step
    assert edit.corrupted_step != "Distance = speed × time = 40 × 0.25 = 10 miles."


def test_math_subtle_refuses_nonequation_step():
    assert math_subtle_edit("The problem asks for a number, not a person.") is None
    assert math_subtle_edit("So he needs at least 325 total.") is None


def test_math_subtle_never_returns_original_value():
    for step in (
        "Total = 5 + 5 = 10",
        "Half = 10 / 2 = 5",
        "Cost = 3 × 4 = 12 dollars",
    ):
        edit = math_subtle_edit(step)
        assert edit is not None
        orig_num = _parse_number(step.split("=")[-1].strip().split()[0])
        new_num = _parse_number(edit.corrupted_step.split("=")[-1].strip().rstrip(" dollars"))
        assert new_num != orig_num


def test_math_implied_answer_linear_chain():
    steps = [
        "Monday to Friday: 5 days",
        "Total sold = 60 + 10 + 20 + 44 + 66 = 200",
        "Notebooks not sold = 800 - 200 = 600",
    ]
    # Corrupt 200 -> 201; downstream "800 - 200" recomputes to 599.
    result = math_implied_answer(steps, 1, Decimal(200), Decimal(201))
    assert result is not None
    answer, note = result
    assert Decimal(answer) == Decimal(599)
    assert "200 -> 201" in note


def test_math_implied_answer_not_defined_on_broken_chain():
    steps = [
        "Total sold = 60 + 10 = 70",
        "The problem asks for a number, not a person.",  # non-equation step
    ]
    assert math_implied_answer(steps, 0, Decimal(70), Decimal(71)) is None


def test_math_implied_answer_chain_broken_by_isolated_edit():
    # Downstream equation does not reference the previous value (the
    # isolated-edit restatement case): not_defined, never a guess.
    steps = [
        "Subtotal = 40 + 20 = 60",
        "Discount = 60 × 0.9 = 54",
    ]
    # Chain intact: works.
    assert math_implied_answer(steps, 0, Decimal(60), Decimal(61)) is not None
    # But if the downstream left operand doesn't match the carried value:
    steps2 = [
        "Subtotal = 40 + 20 = 60",
        "Tax = 54 × 1.1 = 59.4",
    ]
    assert math_implied_answer(steps2, 0, Decimal(60), Decimal(61)) is None


def test_math_reversal_flips_comparison():
    edit = math_reversal_edit("She has more than he does.")
    assert edit is not None and "fewer than" in edit.corrupted_step
    edit = math_reversal_edit("So he needs at least 325 total.")
    assert edit is not None and "at most" in edit.corrupted_step
    assert math_reversal_edit("Total = 5 + 5 = 10") is None


def test_number_parsing_and_format_like():
    assert _parse_number("1,234") == Decimal(1234)
    assert _parse_number("0.25") == Decimal("0.25")
    assert _parse_number("1/2") == Decimal("0.5")
    assert _format_like(Decimal(201), "200") == "201"
    assert _format_like(Decimal("0.26"), "0.25") == "0.26"
    assert "," in _format_like(Decimal(1235), "1,234")


# ---------------------------------------------------------------------------
# Logic operators
# ---------------------------------------------------------------------------

LOGIC_PROBLEM = """There are 2 houses next to each other, numbered 1 on the left and 2 on the right.
 - Each person has different pets: one is a dog owner and one owns horses
 - Each person has a favorite color: one likes blue and one likes white
 - Each person has a favorite drink: one is a root beer lover and one only drinks water

Clue(s):
1. The pianist lives in the first house."""


def test_extract_logic_categories():
    cats = extract_logic_categories(LOGIC_PROBLEM)
    assert ["dog owner", "horses"] in cats or ["dog owner", "owns horses"] in cats
    assert any("blue" in v for cat in cats for v in cat)
    # Every category has >=2 values, none invented.
    assert all(len(c) >= 2 for c in cats)


def test_logic_subtle_swaps_same_category_only():
    cats = extract_logic_categories(LOGIC_PROBLEM)
    edit = logic_subtle_edit("So horses must be in house 1.", cats)
    assert edit is not None
    assert "horses" not in edit.corrupted_step
    assert "dog owner" in edit.corrupted_step  # same category replacement
    assert edit.implied_answer_method is None  # deviation: not_defined
    # No entity from the problem -> no edit (fail closed).
    assert logic_subtle_edit("The answer is house 2.", cats) is None


def test_logic_reversal_negates_relation():
    edit = logic_reversal_edit("white must be in house 1")
    assert edit is not None and "cannot be in house 1" in edit.corrupted_step
    edit = logic_reversal_edit("The percussionist does not live in house 3.")
    assert edit is not None and "does not live" not in edit.corrupted_step
    # Elimination set narrowing.
    edit = logic_reversal_edit("So SUV in {1,2,4}.")
    assert edit is not None and "{1, 2}" in edit.corrupted_step
    assert logic_reversal_edit("Total = 5") is None


# ---------------------------------------------------------------------------
# Factual operators
# ---------------------------------------------------------------------------


def test_factual_subtle_uses_own_context_distractors():
    titles = ["Francis Nethersole", "Gustavus Adolphus of the Palatinate", "Unrelated Person"]
    supporting = ["Francis Nethersole", "Gustavus Adolphus of the Palatinate"]
    step = "Francis Nethersole was born in 1587."
    edit = factual_subtle_edit(step, titles, supporting, ground_truth="Francis Nethersole")
    # Ground truth is never substituted out.
    assert edit is None
    step2 = "Gustavus Adolphus of the Palatinate was born in 1596."
    edit = factual_subtle_edit(step2, titles, supporting, ground_truth="Francis Nethersole")
    assert edit is not None
    assert "Unrelated Person" in edit.corrupted_step  # distractor in
    assert "Gustavus" not in edit.corrupted_step


def test_factual_subtle_fail_closed_without_context_entity():
    assert factual_subtle_edit("The answer is 1587.", ["A", "B"], ["A"], "X") is None
    # No distractors available.
    assert factual_subtle_edit("A was born in 1587.", ["A"], ["A"], "X") is None


def test_factual_reversal_flips_relation():
    edit = factual_reversal_edit("Since 1587 < 1596, Francis was born before Gustavus.")
    assert edit is not None
    assert "born after" in edit.corrupted_step
    edit = factual_reversal_edit("The film was directed by X.")
    assert edit is not None and "produced by" in edit.corrupted_step


# ---------------------------------------------------------------------------
# Ethics operators
# ---------------------------------------------------------------------------


def test_ethics_subtle_uses_reviewed_dictionary_only():
    step = "It involves placing a note on their back without their knowledge."
    edit = ethics_subtle_edit(step)
    assert edit is not None
    assert "with their knowledge" in edit.corrupted_step
    assert edit.implied_answer is None and edit.implied_answer_method is None
    # Off-dictionary text -> fail closed.
    assert ethics_subtle_edit("It could cause embarrassment.") is None


def test_ethics_dictionary_entries_are_fact_swaps():
    for src, dst in ETHICS_FACT_EDITS:
        assert src and dst and src != dst


def test_ethics_reversal_flips_moral_polarity():
    edit = ethics_reversal_edit("This is deceptive and breaks trust.")
    assert edit is not None and "is not deceptive" in edit.corrupted_step
    edit = ethics_reversal_edit("It is a form of deception.")
    assert edit is not None and "is not a form of deception" in edit.corrupted_step
    assert ethics_reversal_edit("The note is yellow.") is None


# ---------------------------------------------------------------------------
# Dispatch (records + operators + spec §4.1 adjustment)
# ---------------------------------------------------------------------------

MATH_COT = (
    "Total sold = 60 + 10 + 20 + 44 + 66 = 200\n"
    "Notebooks not sold = 800 - 200 = 600\n"
    "He started with 800 notebooks total\n"
    "Double check: 600 + 200 = 800\n"
    "The problem asks for notebooks not sold, so the answer is 600"
)


def _math_ctx() -> TaskContext:
    return TaskContext(task_type="math", ground_truth="600")


def test_dispatch_applies_math_subtle_with_implied_answer():
    parse = parse_cot(MATH_COT + "\n\nFinal answer: 600")
    assert parse.ok, parse.validation_errors
    records = build_records("math-002", "resp-1", parse, include_shams=False)
    ctx = _math_ctx()
    report = apply_semantic_edits(records, parse, ctx)
    subtle = [r for r in records if r.corruption_type == "subtle"]
    applied = [r for r in subtle if r.corrupted_step is not None]
    assert applied, report.notes
    for r in applied:
        r.validate()
        assert r.validation_status == "pending"  # E3 review happens later
        assert r.corrupted_cot is not None
    # Deterministic implied answer where the chain recomputes.
    det = [r for r in applied if r.implied_answer_method == "deterministic"]
    for r in det:
        assert r.implied_answer is not None


def test_dispatch_fail_closed_leaves_pending_for_adjudication():
    # A CoT whose steps admit no operator edit at all.
    cot = "banana banana banana\napple apple apple\ncherry cherry cherry\ndurian durian durian\nfig fig fig"
    parse = parse_cot(cot + "\n\nFinal answer: fig")
    assert parse.ok, parse.validation_errors
    records = build_records("math-x", "resp-1", parse, include_shams=False)
    report = apply_semantic_edits(records, parse, _math_ctx())
    subtle_rev = [
        r for r in records if r.corruption_type in ("subtle", "reversal")
    ]
    assert all(r.corrupted_step is None for r in subtle_rev)
    assert set(report.needs_adjudication) == {r.corruption_id for r in subtle_rev}


def test_dispatch_position_adjustment_flagged():
    # Step at early quartile (idx 1 of 5) has no equation; upstream does.
    cot = (
        "We list the givens here as preamble text\n"
        "The problem asks for a number, not a person\n"  # idx 1: no equation
        "Total sold = 60 + 10 = 70\n"
        "Remainder = 100 - 70 = 30\n"
        "So the answer is 30"
    )
    parse = parse_cot(cot + "\n\nFinal answer: 30")
    assert parse.ok, parse.validation_errors
    records = build_records("math-y", "resp-1", parse, include_shams=False)
    report = apply_semantic_edits(records, parse, _math_ctx())
    adjusted = [r for r in records if r.position_adjusted]
    # Any adjustment is recorded, never silent (spec §4.1).
    for r in adjusted:
        assert r.corrupted_step is not None
        assert any(r.corruption_id in n for n in report.notes)


def test_dispatch_logic_records_not_defined_implied():
    cot = (
        "From clue 1: The pianist lives in house 1\n"
        "white must be in house 1\n"
        "So horses must be in house 1, root beer in house 2\n"
        "Then house 1: owns horses, house 2: owns dogs\n"
        "The dog owner lives in house 2"
    )
    parse = parse_cot(cot + "\n\nFinal answer: 2")
    assert parse.ok, parse.validation_errors
    records = build_records("logic-003", "resp-1", parse, include_shams=False)
    ctx = TaskContext(
        task_type="logic",
        ground_truth="2",
        logic_categories=extract_logic_categories(LOGIC_PROBLEM),
    )
    apply_semantic_edits(records, parse, ctx)
    for r in records:
        if r.corruption_type in ("subtle", "reversal") and r.corrupted_step:
            # Deviation 2026-07-24: logic implied answers are not_defined.
            assert r.implied_answer is None
            assert r.implied_answer_method == "not_defined"


def test_dispatch_ignores_nonpending_and_mechanical_records():
    parse = parse_cot(MATH_COT + "\n\nFinal answer: 600")
    assert parse.ok
    records = build_records("math-002", "resp-1", parse, include_shams=False)
    apply_semantic_edits(records, parse, _math_ctx())
    for r in records:
        if r.corruption_type in ("omission", "truncation", "clean"):
            # Dispatch never touches these.
            assert r.position_adjusted is False
