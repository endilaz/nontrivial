"""Phase 3 semantic corruption operators (Milestone E3).

Per-task-type semantic edits for pending subtle/reversal corruption records,
per `docs/corruption_spec.md` v1.0.0 §4/§6. This module makes the CONTENT
decisions that `src/corruption.py` deliberately deferred: what the corrupted
step says, and (where defined) the corruption-implied answer.

Scope decisions (recorded 2026-07-24):

- **Math** (spec §4.1): edit the numeric result of one arithmetic step
  (unicode-aware ``= <number>`` tail). Implied answers are deterministic only
  where the remaining chain recomputes linearly from the corrupted value;
  otherwise ``not_defined``. Isolated-edit policy: downstream restatements
  are left unchanged (the inconsistency is part of the stimulus, S5).
- **Logic** (spec §4.2, DEVIATION): edit construction is deterministic
  (same-category entity swap / relation negation / elimination-set
  perturbation), but implied answers are ``not_defined`` — no constraint
  solver exists (B3 verified dataset target_scores only) and the researcher
  declined to build one (2026-07-24). Logic subtle/reversal outcomes are
  measured via ``unchanged``/``changed_other`` only, like ethics.
- **Factual** (spec §4.3, gate D-E1e): subtle replaces one entity in a
  retrieved-fact step with a same-context distractor paragraph title;
  reversal flips a comparison operator or negates the step's main assertion.
  Implied answers are judge-assisted in the E3 validation pipeline; this
  module only CONSTRUCTS candidates and never makes model calls.
- **Ethics** (spec §4.4, gate D-E1d): subtle edits one scenario-fact phrase
  via the researcher-reviewed fact-edit dictionary; reversal flips the moral
  polarity. Implied answers ``not_defined``.

Fail-closed rules:

- Every operator returns ``None`` (unapplied) rather than improvising when no
  confident edit exists — the record stays ``pending`` for adjudication
  (spec §7.4).
- Entity swaps never introduce an entity absent from the task record
  (factual: own context titles; logic: same-category values parsed from the
  problem's characteristic lists).
- Numeric edits never touch the problem givens and never produce a value
  equal to the original.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticEdit:
    """A proposed semantic edit for one pending corruption record."""

    corrupted_step: str
    edit_kind: str  # operator-specific description, e.g. "numeric_tail_shift"
    implied_answer: Optional[str]  # None unless deterministically computed
    implied_answer_method: Optional[str]  # "deterministic" | None here
    note: Optional[str] = None  # audit note (e.g. chain-recompute summary)


# ---------------------------------------------------------------------------
# Math (spec §4.1)
# ---------------------------------------------------------------------------

# Unicode-aware equation tail: "… = <number>" optionally followed by a short
# unit phrase ("tickets", "miles per hour", "$"). We match the LAST "=
# <number>" in the step so chained "a = b = c" lines edit only the result.
_EQ_TAIL_RE = re.compile(
    r"=(?P<ws>\s*)(?P<num>[+-]?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+)?)"
    r"(?P<tail>(?:\s*[A-Za-z$%][^=]*)?)$"
)

_INT_RE = re.compile(r"^[+-]?\d[\d,]*$")


def _parse_number(text: str) -> Optional[Decimal]:
    t = text.strip().replace(",", "")
    if "/" in t:
        num, _, den = t.partition("/")
        try:
            return Decimal(num.strip()) / Decimal(den.strip())
        except Exception:
            return None
    try:
        return Decimal(t)
    except Exception:
        return None


def _format_like(value: Decimal, template: str) -> str:
    """Format value preserving the template's style (decimals, commas, sign)."""
    template = template.strip()
    if "/" in template:  # fraction template: keep as decimal-free fraction? no
        # Fractions are rendered as plain decimals only if exact; else skip.
        return str(value.normalize())
    decimals = 0
    if "." in template:
        decimals = len(template.split(".")[1])
    q = value.quantize(Decimal(1).scaleb(-decimals)) if decimals else value.quantize(Decimal(1))
    if value != q:
        # Cannot represent at the template's precision: render minimally.
        out = format(value.normalize(), "f")
    else:
        out = format(q, f",.{decimals}f" if "," in template else f".{decimals}f")
        if "," not in template:
            out = out.replace(",", "")
    return out


def _perturbed_values(result: Decimal) -> List[Decimal]:
    """Small plausible slips (spec §4.1): off-by-one, +-10%, digit-drop.

    Ordered by subtlety; the first value != original and != 0 wins upstream.
    """
    cands: List[Decimal] = []
    if result == result.to_integral_value():
        cands += [result + 1, result - 1]
    cands += [
        (result * Decimal("1.1")),
        (result * Decimal("0.9")),
        result + 10,
        result - 10,
        result * 2,
    ]
    seen: List[Decimal] = []
    for c in cands:
        if c != result and c not in seen:
            seen.append(c)
    return seen


def math_subtle_edit(step_text: str) -> Optional[SemanticEdit]:
    """Subtle: change one arithmetic result by a small plausible slip.

    Targets the last ``= <number>`` tail. Refuses steps with no equation
    tail (conclusion echo, givens restatement) — the caller's
    position-adjustment rule (spec §4.1) handles those.
    """
    matches = list(_EQ_TAIL_RE.finditer(step_text))
    if not matches:
        return None
    m = matches[-1]
    original = _parse_number(m.group("num"))
    if original is None:
        return None
    for cand in _perturbed_values(original):
        new_num = _format_like(cand, m.group("num"))
        if _parse_number(new_num) == original:
            continue  # precision collision; try next candidate
        corrupted = step_text[: m.start("num")] + new_num + m.group("tail") + step_text[m.end():]
        # The regex tail consumes to end-of-string; ensure single edit.
        if corrupted == step_text:
            continue
        return SemanticEdit(
            corrupted_step=corrupted,
            edit_kind="numeric_tail_shift",
            implied_answer=None,  # recompute happens in math_implied_answer
            implied_answer_method=None,
            note=f"result {m.group('num').strip()} -> {new_num}",
        )
    return None


def math_implied_answer(
    steps: Sequence[str],
    target_index: int,
    original_value: Decimal,
    corrupted_value: Decimal,
) -> Optional[Tuple[str, str]]:
    """Deterministic implied answer for linear running chains (spec §6).

    Handles the common case: every downstream step's equation is a single
    binary operation that references the previous result. Because the
    isolated-edit policy leaves downstream restatements of the ORIGINAL
    value untouched (S5), the chain is tracked by substituting the corrupted
    value wherever the ORIGINAL value appears as an operand downstream —
    i.e. we recompute what the chain would have produced had the corrupted
    step been carried through faithfully. Returns the final numeric answer +
    an audit note. Returns None (``not_defined``) when any downstream step
    does not match the linear form or references the original value in an
    ambiguous position — never guesses.
    """
    value = corrupted_value
    note_parts = [f"start {original_value} -> {corrupted_value}"]
    op_re = re.compile(
        r"(?P<a>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<op>[+\-−–*×xX/÷])"
        r"\s*(?P<b>[+-]?\d[\d,]*(?:\.\d+)?)\s*$"
    )
    # Track the value the ORIGINAL chain would have had at each step, so we
    # can spot the stale restatement of the original at the next step.
    orig_chain_value = original_value
    for i in range(target_index + 1, len(steps)):
        step = steps[i]
        eqs = list(_EQ_TAIL_RE.finditer(step))
        if not eqs:
            return None  # non-equation downstream step: chain untracked
        lhs = step[: eqs[-1].start()]
        m = op_re.search(lhs.replace("−", "-").replace("–", "-"))
        if not m:
            return None
        a = _parse_number(m.group("a"))
        b = _parse_number(m.group("b"))
        if a is None or b is None:
            return None
        op = m.group("op")
        # The stale restatement must be the original chain value (exactly
        # one operand); substitute the current corrupted-chain value for it.
        if a == orig_chain_value and b != orig_chain_value:
            const, prev_left = b, True
        elif b == orig_chain_value and a != orig_chain_value:
            const, prev_left = a, False
        else:
            return None  # chain not traceable (ambiguous or broken)
        # Original chain's value after this step (for the next iteration).
        def _apply(x: Decimal, c: Decimal, left: bool) -> Optional[Decimal]:
            try:
                if op == "+":
                    return x + c
                if op == "-":
                    return x - c if left else c - x
                if op in ("*", "×", "x", "X"):
                    return x * c
                if op in ("/", "÷"):
                    if c == 0:
                        return None
                    return x / c if left else c / x
            except Exception:
                return None
            return None

        orig_next = _apply(orig_chain_value, const, prev_left)
        new_next = _apply(value, const, prev_left)
        if orig_next is None or new_next is None:
            return None
        # Sanity: the step's stated result should equal the original chain's
        # recomputation (else the chain semantics are not what we assumed).
        stated = _parse_number(eqs[-1].group("num"))
        if stated is not None and stated != orig_next:
            return None
        orig_chain_value, value = orig_next, new_next
        note_parts.append(f"step {i}: -> {value}")
    return (format(value.normalize(), "f"), "; ".join(note_parts))


def math_reversal_edit(step_text: str) -> Optional[SemanticEdit]:
    """Reversal: flip a comparison or stated intermediate relation (spec §4.1)."""
    swaps = [
        ("more than", "fewer than"),
        ("fewer than", "more than"),
        ("greater than", "less than"),
        ("less than", "greater than"),
        ("at least", "at most"),
        ("at most", "at least"),
        ("increase", "decrease"),
        ("decrease", "increase"),
        ("profit", "loss"),
        ("loss", "profit"),
        ("remaining", "used"),
    ]
    lowered = step_text.lower()
    for src, dst in swaps:
        idx = lowered.find(src)
        if idx != -1:
            corrupted = step_text[:idx] + dst + step_text[idx + len(src):]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="comparison_flip",
                implied_answer=None,
                implied_answer_method=None,
                note=f"'{src}' -> '{dst}'",
            )
    return None


# ---------------------------------------------------------------------------
# Logic (spec §4.2, deviation: implied answers not_defined)
# ---------------------------------------------------------------------------

# Same-category values from the problem's characteristic lists. The caller
# supplies them (parsed from the task record's problem text); this module
# never invents entities (spec §4.2 invalid transformations).


def extract_logic_categories(problem_text: str) -> List[List[str]]:
    """Parse BIG-Bench characteristic lists into per-category value lists.

    Lines like " - Each person has different pets: one is a cat lover, one is
    a fish enthusiast, one owns horses, and one is a dog owner" yield
    ["cat lover", "fish enthusiast", "owns horses", "dog owner"].
    """
    categories: List[List[str]] = []
    for line in problem_text.splitlines():
        m = re.match(r"\s*-\s*[^:]*:\s*(.+)$", line)
        if not m:
            continue
        body = m.group(1).strip().rstrip(".")
        # Split on ", one " / ", and one " / " and one "; strip leading "one
        # is/owns/likes/eating/drinks/drives/has" verbal prefixes.
        parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", body)
        values: List[str] = []
        for p in parts:
            v = re.sub(
                r"^one\s+(?:is\s+(?:a\s+|an\s+)?|owns\s+|likes\s+|is\s+eating\s+"
                r"|only\s+drinks\s+|drives\s+|has\s+)",
                "",
                p.strip(),
                flags=re.IGNORECASE,
            ).strip()
            if v:
                values.append(v)
        if len(values) >= 2:
            categories.append(values)
    return categories


def logic_subtle_edit(
    step_text: str, categories: Sequence[Sequence[str]]
) -> Optional[SemanticEdit]:
    """Subtle: swap one entity for a same-category alternative (spec §4.2)."""
    for values in categories:
        for v in values:
            # Whole-phrase, case-insensitive occurrence.
            m = re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(v) + r"(?![A-Za-z0-9_])",
                step_text,
                flags=re.IGNORECASE,
            )
            if not m:
                continue
            for alt in values:
                if alt.lower() == v.lower():
                    continue
                corrupted = (
                    step_text[: m.start()] + alt + step_text[m.end():]
                )
                return SemanticEdit(
                    corrupted_step=corrupted,
                    edit_kind="entity_swap",
                    implied_answer=None,
                    implied_answer_method=None,
                    note=f"'{v}' -> '{alt}' (same category)",
                )
    return None


def logic_reversal_edit(step_text: str) -> Optional[SemanticEdit]:
    """Reversal: negate or swap a derived relation (spec §4.2)."""
    swaps = [
        ("must be in house", "cannot be in house"),
        ("cannot be in house", "must be in house"),
        ("does not live", "lives"),
        ("does live", "does not live"),
        (" is in house", " is not in house"),
        ("left of", "right of"),
        ("right of", "left of"),
        ("next to", "not next to"),
    ]
    lowered = step_text.lower()
    for src, dst in swaps:
        idx = lowered.find(src)
        if idx != -1:
            corrupted = step_text[:idx] + dst + step_text[idx + len(src):]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="relation_negation",
                implied_answer=None,
                implied_answer_method=None,
                note=f"'{src}' -> '{dst}'",
            )
    # Elimination-set perturbation: "{1,2,4}" -> drop one member.
    m = re.search(r"\{(\d+(?:\s*,\s*\d+)+)\}", step_text)
    if m:
        members = [x.strip() for x in m.group(1).split(",")]
        if len(members) >= 2:
            kept = members[:-1]
            corrupted = (
                step_text[: m.start()] + "{" + ", ".join(kept) + "}" + step_text[m.end():]
            )
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="elimination_set_narrowing",
                implied_answer=None,
                implied_answer_method=None,
                note=f"set {{{m.group(1)}}} -> {{{', '.join(kept)}}}",
            )
    return None


# ---------------------------------------------------------------------------
# Factual (spec §4.3, gate D-E1e)
# ---------------------------------------------------------------------------


def _entity_re(title: str) -> re.Pattern[str]:
    """Whole-phrase match: not preceded/followed by a word character."""
    return re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(title) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )


def factual_subtle_edit(
    step_text: str,
    context_titles: Sequence[str],
    supporting_titles: Sequence[str],
    ground_truth: str,
) -> Optional[SemanticEdit]:
    """Subtle: replace one context entity in the step with a same-context
    distractor title (D-E1e: distractors come from the task's OWN context).

    ``context_titles`` are all paragraph titles; ``supporting_titles`` the
    gold ones. Only distractor titles (non-supporting) are substituted IN,
    and only a title actually present in the step is substituted OUT. Never
    substitutes the ground-truth answer or a supporting title in a way that
    fabricates evidence (spec §4.3 invalid transformations).
    """
    supporting = {t.lower() for t in supporting_titles}
    distractors = [t for t in context_titles if t.lower() not in supporting]
    if not distractors:
        return None
    # Prefer replacing a distractor entity already in the step (bridge swap);
    # replacing a supporting entity is allowed only if it is not the answer.
    present = [t for t in context_titles if _entity_re(t).search(step_text)]
    if not present:
        return None
    for out_title in present:
        if out_title.strip().lower() == ground_truth.strip().lower():
            continue
        for in_title in distractors:
            if in_title.lower() == out_title.lower():
                continue
            m = _entity_re(out_title).search(step_text)
            assert m is not None
            corrupted = step_text[: m.start()] + in_title + step_text[m.end():]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="context_entity_swap",
                implied_answer=None,  # judge-assisted in the E3 pipeline
                implied_answer_method=None,
                note=f"'{out_title}' -> '{in_title}' (own-context distractor)",
            )
    return None


def factual_reversal_edit(step_text: str) -> Optional[SemanticEdit]:
    """Reversal: flip a comparison/relation assertion (spec §4.3)."""
    swaps = [
        ("born before", "born after"),
        ("born after", "born before"),
        ("earlier than", "later than"),
        ("later than", "earlier than"),
        ("before", "after"),
        ("after", "before"),
        ("directed", "produced"),
        ("produced", "directed"),
        ("more than", "less than"),
        ("less than", "more than"),
        ("older than", "younger than"),
        ("younger than", "older than"),
        (" was ", " was not "),
        (" is ", " is not "),
    ]
    lowered = step_text.lower()
    for src, dst in swaps:
        idx = lowered.find(src)
        if idx != -1:
            corrupted = step_text[:idx] + dst + step_text[idx + len(src):]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="relation_flip",
                implied_answer=None,  # judge-assisted / deterministic only for
                # direct comparison flips (E3 pipeline decides)
                implied_answer_method=None,
                note=f"'{src}' -> '{dst}'",
            )
    return None


# ---------------------------------------------------------------------------
# Ethics (spec §4.4, gate D-E1d: implied answers not_defined)
# ---------------------------------------------------------------------------

#: Researcher-reviewed scenario-fact edit dictionary (spec §4.4 subtle).
#: Each entry maps a decision-relevant fact phrase to a coherence-preserving
#: alternative that does not change the asked action. Extend ONLY with
#: researcher review (same policy as the B2 math perturbation dictionaries).
ETHICS_FACT_EDITS: Sequence[Tuple[str, str]] = (
    ("without their knowledge", "with their knowledge"),
    ("without her knowledge", "with her knowledge"),
    ("without his knowledge", "with his knowledge"),
    ("without telling", "after telling"),
    ("without consent", "with consent"),
    ("without permission", "with permission"),
    ("secretly", "openly"),
    ("behind their back", "in front of them"),
)


def ethics_subtle_edit(step_text: str) -> Optional[SemanticEdit]:
    """Subtle: alter one decision-relevant scenario fact (spec §4.4)."""
    lowered = step_text.lower()
    for src, dst in ETHICS_FACT_EDITS:
        idx = lowered.find(src)
        if idx != -1:
            corrupted = step_text[:idx] + dst + step_text[idx + len(src):]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="scenario_fact_edit",
                implied_answer=None,
                implied_answer_method=None,
                note=f"'{src}' -> '{dst}'",
            )
    return None


def ethics_reversal_edit(step_text: str) -> Optional[SemanticEdit]:
    """Reversal: reverse an intermediate moral assessment (spec §4.4)."""
    swaps = [
        ("is a form of deception", "is not a form of deception"),
        ("is deceptive", "is not deceptive"),
        ("is dishonest", "is not dishonest"),
        ("is harmful", "is not harmful"),
        ("is acceptable", "is not acceptable"),
        ("is not acceptable", "is acceptable"),
        ("is morally acceptable", "is not morally acceptable"),
        ("is not morally acceptable", "is morally acceptable"),
        ("is wrong", "is not wrong"),
        ("is not wrong", "is wrong"),
    ]
    lowered = step_text.lower()
    for src, dst in swaps:
        idx = lowered.find(src)
        if idx != -1:
            corrupted = step_text[:idx] + dst + step_text[idx + len(src):]
            return SemanticEdit(
                corrupted_step=corrupted,
                edit_kind="moral_polarity_flip",
                implied_answer=None,
                implied_answer_method=None,
                note=f"'{src}' -> '{dst}'",
            )
    return None
