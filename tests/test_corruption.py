"""Tests for src/corruption.py (Milestone E2).

Covers the spec §7 offline gates: deterministic position mapping + short-CoT
policy (D-E1a), span-exact edits with byte-identity outside the target,
omission/truncation boundaries (D-E1b inclusive), stable content-independent
IDs, clean control byte-identical to source reasoning, fail-closed record
invariants, and N/A conditions that are reported, never improvised.
"""

from __future__ import annotations

import pytest

from src.corruption import (
    CORRUPTION_TYPES,
    STATUS_APPROVED,
    STATUS_NA_SHORT_COT,
    STATUS_PENDING,
    CorruptionError,
    CorruptionRecord,
    apply_clean,
    apply_edit,
    apply_mechanical,
    apply_omission,
    apply_truncation,
    assign_positions,
    build_records,
    corruption_id_for,
    plan_conditions,
    record_from_plan,
)
from src.parsing import ParsedStep, ParseResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REASONING = (
    "First, compute the subtotal: 40 + 20 = 60.\n"
    "Then apply the discount: 60 * 0.9 = 54.\n"
    "Next add tax: 54 * 1.1 = 59.4.\n"
    "Finally round to 59."
)


def _parse(n_steps: int = 4, reasoning: str = REASONING) -> ParseResult:
    """Hand-built valid parse with exactly ``n_steps`` covering steps."""
    lines = reasoning.split("\n")[:n_steps]
    text = "\n".join(lines)
    steps = []
    pos = 0
    for i, line in enumerate(lines):
        s, e = pos, pos + len(line)
        steps.append(ParsedStep(i, line, s, e, "line"))
        pos = e + 1  # newline
    return ParseResult(reasoning_text=text, steps=steps)


def _bad_parse() -> ParseResult:
    r = ParseResult(reasoning_text="some text")
    r.validation_errors.append("all parse strategies failed quality checks")
    return r


# ---------------------------------------------------------------------------
# Position mapping (spec §3 + D-E1a)
# ---------------------------------------------------------------------------


def test_positions_n_ge_4_quartiles_distinct():
    a = assign_positions(8)
    assert (a.early, a.middle, a.late) == (2, 4, 6)
    a = assign_positions(5)
    assert (a.early, a.middle, a.late) == (1, 2, 3)
    a = assign_positions(4)
    assert (a.early, a.middle, a.late) == (1, 2, 3)
    for n in range(4, 60):
        a = assign_positions(n)
        assert a.early is not None and a.middle is not None and a.late is not None
        assert a.early < a.middle < a.late


def test_positions_short_cot_policy():
    # n=3: 0/1/2
    a = assign_positions(3)
    assert (a.early, a.middle, a.late) == (0, 1, 2)
    # n=2: middle N/A
    a = assign_positions(2)
    assert (a.early, a.middle, a.late) == (0, None, 1)
    # n=1: late only
    a = assign_positions(1)
    assert (a.early, a.middle, a.late) == (None, None, 0)


def test_positions_fail_closed_on_zero_steps():
    with pytest.raises(CorruptionError):
        assign_positions(0)


# ---------------------------------------------------------------------------
# Condition planning
# ---------------------------------------------------------------------------


def test_plan_yields_13_main_plus_3_sham_conditions():
    conds = plan_conditions("math-001", "resp-1", _parse(4))
    mains = [c for c in conds if not c.sham]
    shams = [c for c in conds if c.sham]
    assert len(mains) == 13
    assert len(shams) == 3
    # 4 families x 3 positions + 1 clean
    for fam in ("subtle", "reversal", "omission", "truncation"):
        assert sum(1 for c in mains if c.corruption_type == fam) == 3
    cleans = [c for c in mains if c.corruption_type == "clean"]
    assert len(cleans) == 1 and cleans[0].position == "none"
    assert all(c.na_reason is None for c in conds)


def test_plan_no_shams_when_excluded():
    conds = plan_conditions("math-001", "resp-1", _parse(4), include_shams=False)
    assert len(conds) == 13
    assert all(not c.sham for c in conds)


def test_plan_short_cot_marks_na_never_improvises():
    # n=2: middle-position conditions (4 families + sham) are N/A.
    conds = plan_conditions("ethics-001", "resp-2", _parse(2))
    na = [c for c in conds if c.na_reason == STATUS_NA_SHORT_COT]
    na_positions = {(c.corruption_type, c.position) for c in na}
    assert all(pos == "middle" for _, pos in na_positions)
    assert sum(1 for c in na if not c.sham) == 4  # subtle/reversal/omission/truncation
    assert sum(1 for c in na if c.sham) == 1
    # N/A conditions carry no target step.
    assert all(c.target_step_index is None for c in na)

    # n=1: only late survives.
    conds = plan_conditions("ethics-002", "resp-3", _parse(1))
    live = [c for c in conds if c.na_reason is None]
    assert all(c.position in ("late", "none") for c in live)


def test_plan_fail_closed_on_bad_parse():
    with pytest.raises(CorruptionError):
        plan_conditions("math-001", "resp-1", _bad_parse())


def test_corruption_ids_stable_and_content_independent():
    id1 = corruption_id_for("math-001", "resp-1", "subtle", "early")
    id2 = corruption_id_for("math-001", "resp-1", "subtle", "early")
    assert id1 == id2 and len(id1) == 16
    # Distinct tuples -> distinct IDs across every condition in the plan.
    conds = plan_conditions("math-001", "resp-1", _parse(4))
    ids = [c.corruption_id for c in conds]
    assert len(ids) == len(set(ids))
    # Re-planning reproduces the same IDs (frozen, reproducible).
    conds2 = plan_conditions("math-001", "resp-1", _parse(4))
    assert ids == [c.corruption_id for c in conds2]


# ---------------------------------------------------------------------------
# Span-exact surgery (spec §7: only intended spans change)
# ---------------------------------------------------------------------------


def test_apply_edit_changes_only_target_span():
    parse = _parse(4)
    out = apply_edit(
        REASONING, parse, 1, "Then apply the discount: 60 * 0.8 = 48.",
        original_step="Then apply the discount: 60 * 0.9 = 54.",
    )
    step = parse.steps[1]
    prefix = REASONING[: step.start_char]
    suffix = REASONING[step.end_char :]
    assert out == prefix + "Then apply the discount: 60 * 0.8 = 48." + suffix
    # Every other step's text survives byte-identically.
    for i, s in enumerate(parse.steps):
        if i != 1:
            assert s.text in out


def test_apply_edit_fail_closed_on_stale_record():
    parse = _parse(4)
    with pytest.raises(CorruptionError, match="does not match"):
        apply_edit(REASONING, parse, 1, "x", original_step="wrong source text")


def test_apply_edit_rejects_empty_and_final_answer():
    parse = _parse(4)
    with pytest.raises(CorruptionError, match="empty"):
        apply_edit(REASONING, parse, 1, "   ", original_step=parse.steps[1].text)
    with pytest.raises(CorruptionError, match="Final answer"):
        apply_edit(
            REASONING, parse, 1, "so Final answer: 48",
            original_step=parse.steps[1].text,
        )


def test_omission_deletes_exactly_one_step_and_rejoins():
    parse = _parse(4)
    out = apply_omission(
        REASONING, parse, 1, original_step=parse.steps[1].text
    )
    lines = REASONING.split("\n")
    expected = "\n".join([lines[0], lines[2], lines[3]])
    assert out == expected
    assert parse.steps[1].text not in out
    for i in (0, 2, 3):
        assert parse.steps[i].text in out


def test_omission_first_and_last_step_boundaries():
    parse = _parse(4)
    lines = REASONING.split("\n")
    out = apply_omission(REASONING, parse, 0, original_step=lines[0])
    assert out == "\n".join(lines[1:])
    assert not out.startswith("\n")
    out = apply_omission(REASONING, parse, 3, original_step=lines[3])
    assert out == "\n".join(lines[:3])
    assert not out.endswith("\n")


def test_truncation_inclusive_deletes_selected_and_following():
    parse = _parse(4)
    lines = REASONING.split("\n")
    # Inclusive (D-E1b): step 1 and everything after is gone.
    out = apply_truncation(REASONING, parse, 1)
    assert out == lines[0]
    out = apply_truncation(REASONING, parse, 0)
    assert out == ""


def test_clean_control_byte_identical():
    assert apply_clean(REASONING) == REASONING
    # And via the record path.
    records = build_records("math-001", "resp-1", _parse(4), include_shams=False)
    clean = [r for r in records if r.corruption_type == "clean"]
    assert len(clean) == 1
    assert clean[0].corrupted_cot == REASONING
    assert clean[0].validation_status == STATUS_APPROVED


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def test_records_from_plan_counts_and_statuses():
    records = build_records("math-001", "resp-1", _parse(4))
    assert len(records) == 16
    pending = [r for r in records if r.validation_status == STATUS_PENDING]
    # subtle(3) + reversal(3) + omission(3) + truncation(3) + sham(3) pending
    assert len(pending) == 15
    for r in records:
        r.validate()  # no record raises
        assert r.schema == "corruption_record"
        assert r.spec_version == "1.0.0"


def test_na_records_terminal_and_empty():
    records = build_records("ethics-001", "resp-2", _parse(2))
    na = [r for r in records if r.validation_status == STATUS_NA_SHORT_COT]
    assert na
    for r in na:
        assert r.corrupted_step is None
        assert r.corrupted_cot is None
        assert r.target_step_index is None
        assert r.implied_answer_method == "not_defined"
        r.validate()


def test_pending_semantic_records_carry_source_step():
    records = build_records("math-001", "resp-1", _parse(4), include_shams=False)
    subtle_early = next(
        r for r in records
        if r.corruption_type == "subtle" and r.position == "early"
    )
    parse = _parse(4)
    assert subtle_early.original_step == parse.steps[1].text  # floor(4/4)=1
    assert subtle_early.corrupted_step is None
    assert subtle_early.corrupted_cot is None
    assert subtle_early.parser_version == parse.parser_version


def test_record_invariants_fail_closed():
    base = dict(
        corruption_id="x",
        task_id="t",
        baseline_response_id="r",
        parser_version="1.2.0",
        corruption_type="subtle",
        position="early",
        target_step_index=1,
        original_step="step",
        corrupted_step=None,
        corrupted_cot=None,
        implied_answer=None,
        implied_answer_method=None,
    )
    # implied answer without a real method
    rec = CorruptionRecord(**{**base, "implied_answer": "42"})
    with pytest.raises(CorruptionError):
        rec.validate()
    # sham with an implied answer
    rec = CorruptionRecord(
        **{**base, "sham": True, "implied_answer": "42",
           "implied_answer_method": "deterministic"}
    )
    with pytest.raises(CorruptionError):
        rec.validate()
    # N/A carrying content
    rec = CorruptionRecord(
        **{**base, "validation_status": STATUS_NA_SHORT_COT,
           "corrupted_cot": "text"}
    )
    with pytest.raises(CorruptionError):
        rec.validate()
    # clean carrying an edit
    rec = CorruptionRecord(
        **{**base, "corruption_type": "clean", "corrupted_step": "edited"}
    )
    with pytest.raises(CorruptionError):
        rec.validate()
    # unknown method / type
    rec = CorruptionRecord(**{**base, "implied_answer_method": "vibes"})
    with pytest.raises(CorruptionError):
        rec.validate()
    rec = CorruptionRecord(**{**base, "corruption_type": "chaos"})
    with pytest.raises(CorruptionError):
        rec.validate()


def test_to_dict_roundtrips_required_fields():
    records = build_records("math-001", "resp-1", _parse(4))
    d = records[0].to_dict()
    for key in (
        "corruption_id", "task_id", "baseline_response_id", "parser_version",
        "corruption_type", "position", "target_step_index", "original_step",
        "corrupted_step", "corrupted_cot", "implied_answer",
        "implied_answer_method", "validation_status", "position_adjusted",
        "na_reason", "sham", "sham_status", "schema", "schema_version",
        "spec_version",
    ):
        assert key in d


# ---------------------------------------------------------------------------
# Mechanical application (omission/truncation)
# ---------------------------------------------------------------------------


def test_apply_mechanical_omission_and_truncation():
    parse = _parse(4)
    records = build_records("math-001", "resp-1", parse, include_shams=False)
    om = next(
        r for r in records
        if r.corruption_type == "omission" and r.position == "middle"
    )
    apply_mechanical(om, parse)
    assert om.corrupted_cot == apply_omission(
        parse.reasoning_text, parse, om.target_step_index,
        original_step=om.original_step,
    )
    assert om.implied_answer_method == "not_defined"

    tr = next(
        r for r in records
        if r.corruption_type == "truncation" and r.position == "early"
    )
    apply_mechanical(tr, parse)
    assert tr.corrupted_cot == apply_truncation(
        parse.reasoning_text, parse, tr.target_step_index
    )


def test_apply_mechanical_rejects_semantic_families_and_nonpending():
    parse = _parse(4)
    records = build_records("math-001", "resp-1", parse, include_shams=False)
    subtle = next(r for r in records if r.corruption_type == "subtle")
    with pytest.raises(CorruptionError, match="semantic"):
        apply_mechanical(subtle, parse)
    clean = next(r for r in records if r.corruption_type == "clean")
    with pytest.raises(CorruptionError, match="not pending"):
        apply_mechanical(clean, parse)


def test_full_edit_path_byte_identity_outside_span():
    """End-to-end spec §7 check: plan -> edit -> only the target span changed."""
    parse = _parse(4)
    records = build_records("math-001", "resp-1", parse, include_shams=False)
    rec = next(
        r for r in records
        if r.corruption_type == "reversal" and r.position == "late"
    )
    assert rec.target_step_index is not None and rec.original_step is not None
    new_step = "Finally round to 60."
    cot = apply_edit(
        parse.reasoning_text, parse, rec.target_step_index, new_step,
        original_step=rec.original_step,
    )
    rec.corrupted_step = new_step
    rec.corrupted_cot = cot
    rec.implied_answer = "60"
    rec.implied_answer_method = "deterministic"
    rec.validate()
    step = parse.steps[rec.target_step_index]
    assert cot[: step.start_char] == REASONING[: step.start_char]
    assert cot.endswith(REASONING[step.end_char :])


def test_corruption_types_constant_matches_spec():
    assert set(CORRUPTION_TYPES) == {
        "subtle", "reversal", "omission", "truncation", "clean"
    }
