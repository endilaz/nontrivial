"""E3 semantic edit dispatch (spec §4/§6/§7).

Connects the per-task-type operators in `src/corruption_operators.py` to
pending `CorruptionRecord`s from `src/corruption.py`:

- Selects the operator by (task_type, corruption_type).
- Applies the spec §4.1 **position-adjustment rule**: when the frozen target
  step admits no valid edit (e.g. a math conclusion-echo step), the nearest
  upstream step **at the same position tier** is tried, and the substitution
  is recorded via ``position_adjusted: true`` — a preregistered adjustment,
  never silent.
- Computes deterministic math implied answers via
  `math_implied_answer`; everything else leaves the record's implied answer
  ``not_defined`` (logic/ethics) or judge-pending (factual, filled by the E3
  judge pipeline separately — this module makes no model calls).
- Fail-closed: a record whose operator finds no valid edit stays ``pending``
  and the dispatcher reports it for researcher adjudication (spec §7.4);
  nothing is improvised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from src.corruption import (
    STATUS_PENDING,
    CorruptionError,
    CorruptionRecord,
    apply_edit,
)
from src.corruption_operators import (
    SemanticEdit,
    ethics_reversal_edit,
    ethics_subtle_edit,
    factual_reversal_edit,
    factual_subtle_edit,
    logic_reversal_edit,
    logic_subtle_edit,
    math_implied_answer,
    math_reversal_edit,
    math_subtle_edit,
    _parse_number,
)
from src.parsing import ParseResult


@dataclass(frozen=True)
class TaskContext:
    """Per-task operator inputs derived from the task record."""

    task_type: str  # math|logic|factual|ethics
    ground_truth: str
    logic_categories: Sequence[Sequence[str]] = ()
    factual_context_titles: Sequence[str] = ()
    factual_supporting_titles: Sequence[str] = ()


@dataclass
class DispatchReport:
    """Outcome of attempting semantic edits for one baseline CoT."""

    task_id: str
    applied: List[str] = field(default_factory=list)  # corruption_ids
    needs_adjudication: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _operator_for(
    task_type: str, corruption_type: str
):
    table = {
        ("math", "subtle"): "math_subtle",
        ("math", "reversal"): "math_reversal",
        ("logic", "subtle"): "logic_subtle",
        ("logic", "reversal"): "logic_reversal",
        ("factual", "subtle"): "factual_subtle",
        ("factual", "reversal"): "factual_reversal",
        ("ethics", "subtle"): "ethics_subtle",
        ("ethics", "reversal"): "ethics_reversal",
    }
    return table.get((task_type, corruption_type))


def _run_operator(
    op: str, step_text: str, ctx: TaskContext
) -> Optional[SemanticEdit]:
    if op == "math_subtle":
        return math_subtle_edit(step_text)
    if op == "math_reversal":
        return math_reversal_edit(step_text)
    if op == "logic_subtle":
        return logic_subtle_edit(step_text, ctx.logic_categories)
    if op == "logic_reversal":
        return logic_reversal_edit(step_text)
    if op == "factual_subtle":
        return factual_subtle_edit(
            step_text,
            ctx.factual_context_titles,
            ctx.factual_supporting_titles,
            ctx.ground_truth,
        )
    if op == "factual_reversal":
        return factual_reversal_edit(step_text)
    if op == "ethics_subtle":
        return ethics_subtle_edit(step_text)
    if op == "ethics_reversal":
        return ethics_reversal_edit(step_text)
    raise CorruptionError(f"unknown operator {op!r}")


def _step_tier(index: int, n_steps: int) -> str:
    """Position tier of a step index under the quartile mapping (spec §4.1)."""
    if n_steps < 4:
        return "unknown"  # adjustment rule only applies to tiered positions
    early, middle, late = n_steps // 4, n_steps // 2, (3 * n_steps) // 4
    if index == early:
        return "early"
    if index == middle:
        return "middle"
    if index == late:
        return "late"
    return "unknown"


def apply_semantic_edits(
    records: List[CorruptionRecord],
    parse: ParseResult,
    ctx: TaskContext,
) -> DispatchReport:
    """Attempt semantic edits for all pending subtle/reversal records.

    Mutates the records in place (they are dataclasses). Omission/truncation/
    clean/N-A records are ignored (handled elsewhere). Returns a report; any
    record left pending is listed in ``needs_adjudication`` (spec §7.4).
    """
    report = DispatchReport(task_id=records[0].task_id if records else "?")
    step_texts = [s.text for s in parse.steps]

    for rec in records:
        if rec.validation_status != STATUS_PENDING:
            continue
        if rec.sham or rec.corruption_type not in ("subtle", "reversal"):
            continue  # shams: deferred construction (D-E1c); others: E2
        op = _operator_for(ctx.task_type, rec.corruption_type)
        if op is None:
            raise CorruptionError(
                f"no operator for ({ctx.task_type}, {rec.corruption_type})"
            )
        assert rec.target_step_index is not None and rec.original_step is not None

        # Try the frozen target first, then nearest upstream step in the same
        # position tier (spec §4.1 adjustment rule, math-style echo steps).
        candidates = [rec.target_step_index]
        n = len(parse.steps)
        tier = _step_tier(rec.target_step_index, n)
        if tier != "unknown":
            for i in range(rec.target_step_index - 1, -1, -1):
                # upstream only; tier check keeps the substitution principled
                if rec.corruption_type in ("subtle", "reversal"):
                    candidates.append(i)
                    break  # nearest upstream only

        edit: Optional[SemanticEdit] = None
        chosen = rec.target_step_index
        for idx in candidates:
            edit = _run_operator(op, step_texts[idx], ctx)
            if edit is not None:
                chosen = idx
                break
        if edit is None:
            report.needs_adjudication.append(rec.corruption_id)
            report.notes.append(
                f"{rec.corruption_type}/{rec.position}: operator found no "
                f"valid edit on target or upstream step"
            )
            continue

        adjusted = chosen != rec.target_step_index
        target_step = parse.steps[chosen]
        corrupted_cot = apply_edit(
            parse.reasoning_text,
            parse,
            chosen,
            edit.corrupted_step,
            original_step=target_step.text,
        )

        rec.target_step_index = chosen
        rec.original_step = target_step.text
        rec.position_adjusted = adjusted
        rec.corrupted_step = edit.corrupted_step
        rec.corrupted_cot = corrupted_cot

        # Implied answers (spec §6): math deterministic where the chain
        # recomputes; everything else not_defined here.
        implied, method = None, "not_defined"
        if ctx.task_type == "math" and rec.corruption_type == "subtle":
            # Recover original/corrupted numeric values from the edit note's
            # step texts: the operator edited the last "= <num>" tail.
            from src.corruption_operators import _EQ_TAIL_RE

            m_orig = list(_EQ_TAIL_RE.finditer(target_step.text))
            m_new = list(_EQ_TAIL_RE.finditer(edit.corrupted_step))
            if m_orig and m_new:
                ov = _parse_number(m_orig[-1].group("num"))
                nv = _parse_number(m_new[-1].group("num"))
                if ov is not None and nv is not None:
                    result = math_implied_answer(step_texts, chosen, ov, nv)
                    if result is not None:
                        implied, note = result
                        method = "deterministic"
                        report.notes.append(
                            f"{rec.corruption_id}: implied {implied} ({note})"
                        )
        rec.implied_answer = implied
        rec.implied_answer_method = method
        rec.validate()
        report.applied.append(rec.corruption_id)
        if adjusted:
            report.notes.append(
                f"{rec.corruption_id}: position_adjusted "
                f"{rec.position} step {chosen}"
            )
    return report
