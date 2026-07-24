"""Phase 3 step corruption primitives (Milestone E2).

Implements the fixed mechanics of `docs/corruption_spec.md` v1.0.0 (approved
2026-07-24):

- Deterministic, preregistered position mapping on parsed steps
  (early/middle/late quartiles; short-CoT policy, gate D-E1a).
- Span-exact text surgery on the visible reasoning: only the target step's
  span may change for subtle/reversal/sham edits; omission deletes exactly
  the step's span; truncation deletes the selected step **inclusive** and all
  following steps (gate D-E1b); the clean control is byte-identical.
- Structured corruption records (plan.md §E2 + spec §8): stable IDs, frozen
  position assignment, ``position_adjusted``, ``na_reason``, ``sham``,
  ``sham_status``, and ``implied_answer_method``. ``not_defined`` is allowed
  only for the implied answer, never for condition presence (spec §2).

This module performs **no content selection and no model calls**. Choosing
WHAT the corrupted step says (the semantic edit), deriving implied answers,
and constructing sham rewrites are separate, gated steps (E3) that consume
the :class:`CorruptionPlan` produced here. Records whose semantic edit has not
been applied/reviewed yet carry ``validation_status: "pending"``.

Fail-closed rules:

- A plan may only be built from a parse with no validation errors and at
  least one step.
- The eligibility population and position assignments are frozen before any
  corruption text is generated (S3/S12); this module never inspects step
  content to pick positions.
- Applying an edit to the wrong span, an empty replacement, or a replacement
  containing "Final answer" raises.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional

from src.parsing import ParseResult

CORRUPTION_SPEC_VERSION = "1.0.0"
CORRUPTION_RECORD_SCHEMA = "corruption_record"
CORRUPTION_RECORD_SCHEMA_VERSION = 1

# Corruption families (spec §2). "clean" is the re-injection null control.
CORRUPTION_TYPES = ("subtle", "reversal", "omission", "truncation", "clean")
POSITIONS = ("early", "middle", "late")

# validation_status values (spec §E2 record schema + §3.1)
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_NA_SHORT_COT = "na_short_cot"

IMPLIED_ANSWER_METHODS = ("deterministic", "judge", "not_defined")


class CorruptionError(RuntimeError):
    """Fail-closed corruption construction/validation error."""


# ---------------------------------------------------------------------------
# Position mapping (spec §3, gate D-E1a) — frozen before text generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionAssignment:
    """Frozen early/middle/late step indices for one CoT."""

    n_steps: int
    early: Optional[int]
    middle: Optional[int]
    late: Optional[int]

    def index_for(self, position: str) -> Optional[int]:
        if position == "early":
            return self.early
        if position == "middle":
            return self.middle
        if position == "late":
            return self.late
        raise CorruptionError(f"unknown position {position!r}")


def assign_positions(n_steps: int) -> PositionAssignment:
    """Deterministic quartile mapping with the approved short-CoT policy.

    n>=4: early=floor(n/4), middle=floor(n/2), late=floor(3n/4) — always three
    distinct indices. n==3: 0/1/2. n==2: early=0, late=1, middle N/A.
    n==1: late=0 only, early/middle N/A (spec §3.1).
    """
    if n_steps < 1:
        raise CorruptionError("cannot assign positions: CoT has no steps")
    if n_steps == 1:
        return PositionAssignment(n_steps, None, None, 0)
    if n_steps == 2:
        return PositionAssignment(n_steps, 0, None, 1)
    if n_steps == 3:
        return PositionAssignment(n_steps, 0, 1, 2)
    early, middle, late = n_steps // 4, n_steps // 2, (3 * n_steps) // 4
    # Quartiles are monotonically non-decreasing; for n>=4 distinctness
    # follows from floor(n/4) < floor(n/2) < floor(3n/4). Guard anyway —
    # a violated invariant here is a bug, not a short CoT.
    if not (early < middle < late):
        raise CorruptionError(
            f"position invariant violated for n_steps={n_steps}: "
            f"{early=}, {middle=}, {late=}"
        )
    return PositionAssignment(n_steps, early, middle, late)


# ---------------------------------------------------------------------------
# Corruption plan: the frozen set of conditions for one baseline CoT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedCondition:
    """One of the 13 conditions for one (task, baseline response) pair."""

    corruption_id: str
    task_id: str
    baseline_response_id: str
    corruption_type: str  # subtle|reversal|omission|truncation|clean
    position: str  # early|middle|late|none
    target_step_index: Optional[int]  # None for clean and N/A conditions
    na_reason: Optional[str]  # STATUS_NA_SHORT_COT or None
    sham: bool = False


def corruption_id_for(
    task_id: str,
    baseline_response_id: str,
    corruption_type: str,
    position: str,
    *,
    sham: bool = False,
) -> str:
    """Stable, content-independent condition ID (spec §E2 "stable ID").

    Derived from the identifying tuple only — never from generated text — so
    IDs are reproducible before and after semantic edits are applied.
    """
    key = "|".join(
        [
            "corruption",
            CORRUPTION_SPEC_VERSION,
            task_id,
            baseline_response_id,
            corruption_type,
            position,
            "sham" if sham else "main",
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def plan_conditions(
    task_id: str,
    baseline_response_id: str,
    parse: ParseResult,
    *,
    include_shams: bool = True,
) -> List[PlannedCondition]:
    """Freeze the 13 (+3 sham) conditions for one eligible baseline CoT.

    Eligibility (spec §2.1) is decided by the caller; this function requires
    a clean parse with >=1 step and never inspects step content (S3).
    """
    if parse.validation_errors:
        raise CorruptionError(
            f"cannot plan corruptions for {task_id}: parse failed "
            f"closed with errors {parse.validation_errors!r}"
        )
    n = len(parse.steps)
    if n < 1:
        raise CorruptionError(f"cannot plan corruptions for {task_id}: no steps")

    assignment = assign_positions(n)
    conditions: List[PlannedCondition] = []

    def _add(ctype: str, position: str, *, sham: bool = False) -> None:
        idx = assignment.index_for(position)
        na = STATUS_NA_SHORT_COT if idx is None else None
        conditions.append(
            PlannedCondition(
                corruption_id=corruption_id_for(
                    task_id,
                    baseline_response_id,
                    "sham" if sham else ctype,
                    position,
                    sham=sham,
                ),
                task_id=task_id,
                baseline_response_id=baseline_response_id,
                corruption_type="sham" if sham else ctype,
                position=position,
                target_step_index=idx,
                na_reason=na,
                sham=sham,
            )
        )

    for ctype in ("subtle", "reversal", "omission", "truncation"):
        for position in POSITIONS:
            _add(ctype, position)
    conditions.append(
        PlannedCondition(
            corruption_id=corruption_id_for(
                task_id, baseline_response_id, "clean", "none"
            ),
            task_id=task_id,
            baseline_response_id=baseline_response_id,
            corruption_type="clean",
            position="none",
            target_step_index=None,
            na_reason=None,
        )
    )
    if include_shams:
        for position in POSITIONS:
            _add("sham", position, sham=True)
    return conditions


# ---------------------------------------------------------------------------
# Span-exact text surgery (spec §4/§5)
# ---------------------------------------------------------------------------


def _step_span(parse: ParseResult, step_index: int) -> tuple[int, int]:
    if not (0 <= step_index < len(parse.steps)):
        raise CorruptionError(f"step index {step_index} out of range")
    step = parse.steps[step_index]
    return step.start_char, step.end_char


def _verify_span_source(
    reasoning: str, start: int, end: int, original_step: str
) -> None:
    """The edit must target exactly the source step's span (spec §E2 tests)."""
    if reasoning[start:end].strip() != original_step.strip():
        raise CorruptionError(
            "original_step does not match the target span — refusing to edit "
            f"(span says {reasoning[start:end].strip()!r}, "
            f"record says {original_step.strip()!r})"
        )


def apply_edit(
    reasoning: str,
    parse: ParseResult,
    step_index: int,
    replacement: str,
    *,
    original_step: str,
) -> str:
    """Replace exactly one step's span; byte-identical elsewhere (spec §4).

    ``original_step`` must match the recorded source step text: the edit is
    anchored to the span AND its expected content, so a stale record cannot
    silently edit the wrong text. ``replacement`` must be non-empty and must
    not introduce a "Final answer" line (spec §4 general rule).
    """
    if not replacement.strip():
        raise CorruptionError("replacement step text is empty")
    if "final answer" in replacement.lower():
        raise CorruptionError(
            'replacement must not introduce the string "Final answer"'
        )
    start, end = _step_span(parse, step_index)
    _verify_span_source(reasoning, start, end, original_step)
    return reasoning[:start] + replacement.strip() + reasoning[end:]


def apply_omission(
    reasoning: str,
    parse: ParseResult,
    step_index: int,
    *,
    original_step: str,
) -> str:
    """Delete exactly the target step's span (spec §5), rejoining cleanly.

    Only the step's own span plus (when it is not the last step) the
    following inter-step whitespace is removed; every other character is
    preserved. Deleting the last step trims preceding whitespace instead, so
    no new trailing whitespace is introduced.
    """
    start, end = _step_span(parse, step_index)
    _verify_span_source(reasoning, start, end, original_step)
    if step_index < len(parse.steps) - 1:
        next_start = parse.steps[step_index + 1].start_char
        # Gap must be whitespace-only (parser coverage guarantee).
        if reasoning[end:next_start].strip():
            raise CorruptionError("non-whitespace gap between steps")
        return reasoning[:start] + reasoning[next_start:]
    # Last step: remove preceding gap whitespace as well.
    if step_index > 0:
        prev_end = parse.steps[step_index - 1].end_char
        if reasoning[prev_end:start].strip():
            raise CorruptionError("non-whitespace gap between steps")
        return reasoning[:prev_end]
    return ""


def apply_truncation(
    reasoning: str,
    parse: ParseResult,
    step_index: int,
) -> str:
    """Delete the selected step INCLUSIVE and all following steps (D-E1b)."""
    start, _end = _step_span(parse, step_index)
    if step_index > 0:
        prev_end = parse.steps[step_index - 1].end_char
        if reasoning[prev_end:start].strip():
            raise CorruptionError("non-whitespace gap between steps")
        return reasoning[:prev_end]
    return ""


def apply_clean(reasoning: str) -> str:
    """Clean re-injection control: byte-identical source reasoning (spec §2)."""
    return reasoning


# ---------------------------------------------------------------------------
# Structured corruption record (plan.md §E2 + spec §8)
# ---------------------------------------------------------------------------


@dataclass
class CorruptionRecord:
    """One corruption condition, from plan through validation."""

    corruption_id: str
    task_id: str
    baseline_response_id: str
    parser_version: str
    corruption_type: str  # subtle|reversal|omission|truncation|clean|sham
    position: str  # early|middle|late|none
    target_step_index: Optional[int]
    original_step: Optional[str]  # None for clean/N-A conditions
    corrupted_step: Optional[str]  # None until the semantic edit is applied
    corrupted_cot: Optional[str]  # full re-injection reasoning text
    implied_answer: Optional[str]
    implied_answer_method: Optional[str]  # deterministic|judge|not_defined
    validation_status: str = STATUS_PENDING
    spec_version: str = CORRUPTION_SPEC_VERSION
    sham: bool = False
    sham_status: Optional[str] = None  # None | "constructible" | "not_constructible"
    position_adjusted: bool = False
    na_reason: Optional[str] = None
    schema: str = CORRUPTION_RECORD_SCHEMA
    schema_version: int = CORRUPTION_RECORD_SCHEMA_VERSION

    def validate(self) -> None:
        """Fail-closed record invariants (spec §E2/§8)."""
        if self.corruption_type not in CORRUPTION_TYPES and not self.sham:
            raise CorruptionError(
                f"unknown corruption_type {self.corruption_type!r}"
            )
        if self.implied_answer_method is not None and (
            self.implied_answer_method not in IMPLIED_ANSWER_METHODS
        ):
            raise CorruptionError(
                f"unknown implied_answer_method {self.implied_answer_method!r}"
            )
        if self.validation_status == STATUS_NA_SHORT_COT:
            # N/A conditions carry no edit content, ever (spec §3.1).
            if (
                self.corrupted_step is not None
                or self.corrupted_cot is not None
                or self.target_step_index is not None
            ):
                raise CorruptionError(
                    "N/A (short-CoT) condition must not carry edit content"
                )
        if self.corruption_type == "clean":
            # Clean control is byte-identical to the source reasoning and is
            # never pending: it needs no semantic edit (spec §7 test 1).
            if self.corrupted_step is not None:
                raise CorruptionError("clean control must not edit a step")
        if self.implied_answer is not None and self.implied_answer_method in (
            None,
            "not_defined",
        ):
            raise CorruptionError(
                "implied_answer present but method is missing/not_defined"
            )
        if self.sham and self.implied_answer is not None:
            raise CorruptionError(
                "sham conditions have no implied answer by construction"
            )

    def to_dict(self) -> dict:
        self.validate()
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "spec_version": self.spec_version,
            "corruption_id": self.corruption_id,
            "task_id": self.task_id,
            "baseline_response_id": self.baseline_response_id,
            "parser_version": self.parser_version,
            "corruption_type": self.corruption_type,
            "position": self.position,
            "target_step_index": self.target_step_index,
            "original_step": self.original_step,
            "corrupted_step": self.corrupted_step,
            "corrupted_cot": self.corrupted_cot,
            "implied_answer": self.implied_answer,
            "implied_answer_method": self.implied_answer_method,
            "validation_status": self.validation_status,
            "sham": self.sham,
            "sham_status": self.sham_status,
            "position_adjusted": self.position_adjusted,
            "na_reason": self.na_reason,
        }


def record_from_plan(
    condition: PlannedCondition,
    parse: ParseResult,
) -> CorruptionRecord:
    """Instantiate the pending record for a planned condition.

    Semantic content (corrupted_step/corrupted_cot/implied answer) stays
    unset until E3; N/A short-CoT conditions are born terminal.
    """
    if condition.na_reason is not None:
        return CorruptionRecord(
            corruption_id=condition.corruption_id,
            task_id=condition.task_id,
            baseline_response_id=condition.baseline_response_id,
            parser_version=parse.parser_version,
            corruption_type=condition.corruption_type,
            position=condition.position,
            target_step_index=None,
            original_step=None,
            corrupted_step=None,
            corrupted_cot=None,
            implied_answer=None,
            implied_answer_method="not_defined",
            validation_status=STATUS_NA_SHORT_COT,
            sham=condition.sham,
            sham_status=(
                "not_constructible" if condition.sham else None
            ),
            na_reason=condition.na_reason,
        )
    if condition.corruption_type == "clean":
        return CorruptionRecord(
            corruption_id=condition.corruption_id,
            task_id=condition.task_id,
            baseline_response_id=condition.baseline_response_id,
            parser_version=parse.parser_version,
            corruption_type="clean",
            position="none",
            target_step_index=None,
            original_step=None,
            corrupted_step=None,
            corrupted_cot=apply_clean(parse.reasoning_text),
            implied_answer=None,
            implied_answer_method="not_defined",
            validation_status=STATUS_APPROVED,
        )
    assert condition.target_step_index is not None
    step = parse.steps[condition.target_step_index]
    return CorruptionRecord(
        corruption_id=condition.corruption_id,
        task_id=condition.task_id,
        baseline_response_id=condition.baseline_response_id,
        parser_version=parse.parser_version,
        corruption_type=condition.corruption_type,
        position=condition.position,
        target_step_index=condition.target_step_index,
        original_step=step.text,
        corrupted_step=None,
        corrupted_cot=None,
        implied_answer=None,
        implied_answer_method=None,
        validation_status=STATUS_PENDING,
        sham=condition.sham,
        sham_status="constructible" if condition.sham else None,
    )


def build_records(
    task_id: str,
    baseline_response_id: str,
    parse: ParseResult,
    *,
    include_shams: bool = True,
) -> List[CorruptionRecord]:
    """Plan + instantiate all condition records for one baseline CoT."""
    records: List[CorruptionRecord] = []
    for condition in plan_conditions(
        task_id, baseline_response_id, parse, include_shams=include_shams
    ):
        record = record_from_plan(condition, parse)
        record.validate()
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Mechanical condition application (omission/truncation need no semantic edit)
# ---------------------------------------------------------------------------


def apply_mechanical(
    record: CorruptionRecord, parse: ParseResult
) -> CorruptionRecord:
    """Fill corrupted_cot for omission/truncation records (no content choice).

    These two families are fully determined by the frozen position and the
    parse spans, so they complete without any semantic decision and are
    marked approved pending the E3 transcript review. Subtle/reversal/sham
    records raise: their corrupted step text is a semantic, per-task-type
    decision (spec §4) produced by the E3 pipeline and researcher review,
    then written via :func:`apply_edit`.
    """
    if record.validation_status != STATUS_PENDING:
        raise CorruptionError(
            f"record {record.corruption_id} is not pending "
            f"({record.validation_status})"
        )
    if record.corruption_type not in ("omission", "truncation"):
        raise CorruptionError(
            f"{record.corruption_type} requires a semantic edit (E3), not "
            "mechanical application"
        )
    if record.target_step_index is None or record.original_step is None:
        raise CorruptionError("mechanical record missing target step")
    reasoning = parse.reasoning_text
    if record.corruption_type == "omission":
        cot = apply_omission(
            reasoning,
            parse,
            record.target_step_index,
            original_step=record.original_step,
        )
    else:
        cot = apply_truncation(reasoning, parse, record.target_step_index)
    record.corrupted_cot = cot
    record.corrupted_step = None  # no replacement text exists for deletions
    # Omission/truncation never carry an implied answer (spec §6).
    record.implied_answer = None
    record.implied_answer_method = "not_defined"
    record.validate()
    return record
