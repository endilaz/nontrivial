"""Answer extraction / equivalence / outcome coding (CLAUDE.md Section 3).

Milestone C1 (plan.md §C1) separates deterministic scoring from LLM judging:

* Deterministic comparison runs first and covers numeric canonicalization
  (commas, signs, decimals, simple fractions, percents, and simple trailing
  units), strict yes/no canonicalization, and normalized short-text exact
  matching against a task-specific approved alias list. Substring containment
  is never treated as equivalence.
* Every comparison returns a structured :class:`EvaluationResult` recording
  the method, normalized values, decision, and a human-readable reason.
* The LLM equivalence judge (:func:`judge_equivalence`) is a separate,
  configured experiment routed through ``GenerationClient`` with a versioned
  rubric, a frozen prompt template, JSON-schema replies, and per-call judgment
  records carrying the ``judgment`` schema envelope. It is fail-closed: it
  raises unless its config is explicitly enabled AND its provenance records a
  ``status: approved`` C3 validation. Until then, answers that need the judge
  are reported ``unscored`` — never incorrect, never silently judged.

The final-answer parser is intentionally permissive about surrounding
whitespace and light markdown emphasis, and it takes the LAST ``Final
answer:`` occurrence so a restated answer wins. It returns ``None`` when no
such line exists so downstream code can flag unparseable responses honestly.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src import receipts as receipts_mod
from src import schemas

# Match "Final answer: <rest of line>" case-insensitively. `.+` (no DOTALL)
# stops at the newline, keeping it to a single line as specified.
_FINAL_ANSWER_RE = re.compile(r"final\s+answer\s*:\s*(.+)", re.IGNORECASE)


def parse_final_answer(text: Optional[str]) -> Optional[str]:
    """Extract the answer from the constrained ``Final answer: X`` line.

    Returns the stripped answer string (units and multi-word answers preserved),
    or ``None`` if no ``Final answer:`` line is present or the answer is empty.

    Examples
    --------
    >>> parse_final_answer("... reasoning ...\\nFinal answer: 42")
    '42'
    >>> parse_final_answer("Final answer: 42 apples   ")
    '42 apples'
    >>> parse_final_answer("**Final answer:** yes")
    'yes'
    >>> parse_final_answer("no final line here") is None
    True
    """
    if not text:
        return None
    matches = _FINAL_ANSWER_RE.findall(text)
    if not matches:
        return None
    answer = matches[-1].strip()
    # Strip light markdown emphasis (e.g. "**yes**") that models sometimes add.
    answer = answer.strip("*").strip()
    return answer or None


# --------------------------------------------------------------------------- #
# C1: structured evaluation results
# --------------------------------------------------------------------------- #

#: Decisions a comparison can reach. ``unscored`` means a non-exact candidate
#: would require the (still unvalidated) LLM judge; it is neither correct nor
#: incorrect and must be reported as its own category (plan.md §C1/C2).
DECISIONS = ("equivalent", "not_equivalent", "unscored")


@dataclass(frozen=True)
class EvaluationResult:
    """Structured outcome of one answer comparison (plan.md §C1)."""

    decision: str  # one of DECISIONS
    method: str  # e.g. "numeric_exact", "alias_exact", "llm_judge"
    normalized_prediction: Optional[str]
    normalized_ground_truth: Optional[str]
    reason: str
    alias_matched: Optional[str] = None
    judge: Optional["JudgeReply"] = None

    @property
    def is_equivalent(self) -> Optional[bool]:
        """True/False for decided results; None for unscored."""
        if self.decision == "equivalent":
            return True
        if self.decision == "not_equivalent":
            return False
        return None


# --------------------------------------------------------------------------- #
# C1: numeric canonicalization
# --------------------------------------------------------------------------- #

_INT_WITH_COMMAS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+$")
_SIGNED_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")

#: Leading currency symbols stripped before numeric parsing (presentation,
#: not content). Exactly one symbol is stripped; "$$5" is not a number.
_CURRENCY_SYMBOLS = frozenset("$€£¥₹")
_FRACTION_RE = re.compile(r"^([+-]?\d+)\s*/\s*(\d+)$")
_MIXED_NUMBER_RE = re.compile(r"^([+-]?\d+)\s+(\d+)\s*/\s*(\d+)$")
_LATEX_FRAC_RE = re.compile(r"^\\d?frac\{([+-]?\d+)\}\{(\d+)\}$")
_PERCENT_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:%|percent)$")

#: Trailing-unit pattern: a number followed by whitespace and 1-3 alphabetic
#: tokens (e.g. "42 apples", "3.5 square meters"). Hyphenated/slashed unit
#: text is deliberately NOT stripped (it is ambiguous with ranges/rates).
_TRAILING_UNIT_RE = re.compile(
    r"^(?P<num>.+?)\s+(?P<unit>[A-Za-z]+(?:\s[A-Za-z]+){0,2})$"
)

#: Answers shorter than this (normalized) that fail an exact text match are
#: treated as potential alias/judge cases and reported ``unscored`` rather
#: than ``not_equivalent``. Longer free-text answers that mismatch exactly
#: are decided ``not_equivalent`` deterministically (they are paraphrase
#: failures, not near-misses the judge could rescue). The comparison is
#: word-based so a long ground truth does not push a short prediction over
#: the threshold.
UNSCORED_TEXT_MAX_WORDS = 8


@dataclass(frozen=True)
class NumericParse:
    """A canonicalized numeric answer."""

    value: Decimal
    unit: Optional[str] = None
    is_percent: bool = False
    had_fraction: bool = False

    @property
    def canonical(self) -> str:
        return format(self.value.normalize(), "f")


def _decimal_from_mantissa(text: str) -> Decimal:
    """Parse a plain/commad integer or decimal without exponent notation."""
    candidate = text.strip()
    if _INT_WITH_COMMAS_RE.fullmatch(candidate):
        candidate = candidate.replace(",", "")
    if not _SIGNED_NUMBER_RE.fullmatch(candidate):
        raise InvalidOperation(f"not a plain number: {text!r}")
    return Decimal(candidate)


def parse_numeric(text: Optional[str]) -> Optional[NumericParse]:
    """Canonicalize a numeric answer, or return None if not plainly numeric.

    Handles a single leading currency symbol (``$660``, ``€9,080`` — stripped
    before parsing; researcher decision 2026-07-22 after the full baseline
    showed GSM8K-style answers habitually carry ``$``), commas (``56,551``),
    signs, decimals, simple fractions (``3/4``), mixed numbers (``1 1/2``),
    ``\\frac{a}{b}``, percents (``75%`` -> ``0.75``), and simple trailing
    units (``42 apples``). Exponent notation is intentionally rejected as
    out-of-scope for these tasks.
    """
    if text is None:
        return None
    candidate = " ".join(text.strip().split())
    if not candidate:
        return None

    # A single leading currency symbol is presentation, not content.
    if candidate[0] in _CURRENCY_SYMBOLS:
        stripped = candidate[1:].strip()
        if stripped and stripped[0] not in _CURRENCY_SYMBOLS:
            candidate = stripped

    # Whole-string parse first so percents and fractions win over unit peel
    # ("75 percent" is a percent, not 75 with unit "percent").
    core = _try_parse_number_core(candidate)
    if core is not None:
        value, is_percent, had_fraction = core
        return NumericParse(value=value, unit=None, is_percent=is_percent, had_fraction=had_fraction)

    unit: Optional[str] = None
    unit_match = _TRAILING_UNIT_RE.fullmatch(candidate)
    if unit_match:
        # Only peel a unit when the head parses numerically; otherwise the
        # whole string is text, not a number-with-unit.
        head = unit_match.group("num")
        core = _try_parse_number_core(head)
        if core is not None:
            candidate = head
            unit = unit_match.group("unit").casefold()

    if core is None:
        return None
    value, is_percent, had_fraction = core
    return NumericParse(value=value, unit=unit, is_percent=is_percent, had_fraction=had_fraction)


def _decimal_from_fraction(frac: Fraction) -> Decimal:
    """Exact Fraction -> Decimal (high working precision; terminating for the
    denominators these tasks produce)."""
    return Decimal(frac.numerator) / Decimal(frac.denominator)


def _try_parse_number_core(candidate: str) -> Optional[tuple[Decimal, bool, bool]]:
    percent_match = _PERCENT_RE.fullmatch(candidate)
    if percent_match:
        try:
            return (_decimal_from_mantissa(percent_match.group(1)) / 100, True, False)
        except InvalidOperation:
            return None

    frac_match = _FRACTION_RE.fullmatch(candidate) or _LATEX_FRAC_RE.fullmatch(candidate)
    if frac_match:
        numerator, denominator = int(frac_match.group(1)), int(frac_match.group(2))
        if denominator == 0:
            return None
        return (_decimal_from_fraction(Fraction(numerator, denominator)), False, True)

    mixed_match = _MIXED_NUMBER_RE.fullmatch(candidate)
    if mixed_match:
        whole, num, den = (int(mixed_match.group(i)) for i in (1, 2, 3))
        if den == 0:
            return None
        sign = -1 if whole < 0 else 1
        return (_decimal_from_fraction(Fraction(whole) + sign * Fraction(num, den)), False, True)

    try:
        return (_decimal_from_mantissa(candidate), False, False)
    except InvalidOperation:
        return None


def numeric_equivalent(
    prediction: Optional[str],
    ground_truth: Optional[str],
    *,
    tolerance: Optional[Decimal] = None,
) -> EvaluationResult:
    """Numeric comparison with canonicalized values and optional tolerance.

    A tolerance is a task-approved absolute tolerance (plan.md §C1); it must
    be supplied explicitly per task/run, never defaulted. Unit policy
    (researcher decision, 2026-07-22): units never make an answer incorrect.
    A unit on only one side is ignored (ground truths in this task set are
    bare numbers; models habitually attach units). Conflicting units on BOTH
    sides are genuinely ambiguous and come back ``unscored``, not incorrect.
    """
    if tolerance is not None and tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    pred = parse_numeric(prediction)
    truth = parse_numeric(ground_truth)
    if truth is None:
        raise ValueError(f"ground truth is not numeric: {ground_truth!r}")
    if pred is None:
        return EvaluationResult(
            decision="not_equivalent",
            method="numeric_exact",
            normalized_prediction=None,
            normalized_ground_truth=truth.canonical,
            reason="prediction is not a parseable number",
        )
    if pred.unit and truth.unit and pred.unit != truth.unit:
        return EvaluationResult(
            decision="unscored",
            method="numeric_exact",
            normalized_prediction=f"{pred.canonical} {pred.unit}",
            normalized_ground_truth=f"{truth.canonical} {truth.unit}",
            reason=f"conflicting units ({pred.unit!r} vs {truth.unit!r}); needs review",
        )
    diff = abs(pred.value - truth.value)
    ok = diff <= tolerance if tolerance is not None else pred.value == truth.value
    method = "numeric_tolerance" if tolerance is not None else "numeric_exact"
    unit_note = ""
    if pred.unit != truth.unit:
        unit_note = f"; one-sided unit {pred.unit or truth.unit!r} ignored"
    return EvaluationResult(
        decision="equivalent" if ok else "not_equivalent",
        method=method,
        normalized_prediction=pred.canonical,
        normalized_ground_truth=truth.canonical,
        reason=(
            f"|{pred.canonical} - {truth.canonical}| = {diff.normalize():f}"
            + (f" vs tolerance {tolerance:f}" if tolerance is not None else "")
            + unit_note
        ),
    )


# --------------------------------------------------------------------------- #
# C1: yes/no canonicalization (unambiguous variants only)
# --------------------------------------------------------------------------- #

_YES_VARIANTS = {"yes", "y", "true", "correct", "affirmative"}
_NO_VARIANTS = {"no", "n", "false", "incorrect", "negative"}


def normalize_yes_no(text: Optional[str]) -> Optional[str]:
    """Return ``"yes"``/``"no"`` for unambiguous variants, else None.

    Ambiguous or elaborated answers ("yes, but...", "probably") return None
    so they are surfaced honestly instead of coerced (global rule 7).
    """
    normalized = normalize_text(text)
    if normalized in _YES_VARIANTS:
        return "yes"
    if normalized in _NO_VARIANTS:
        return "no"
    return None


def yes_no_equivalent(prediction: Optional[str], ground_truth: Optional[str]) -> EvaluationResult:
    pred = normalize_yes_no(prediction)
    truth = normalize_yes_no(ground_truth)
    if truth is None:
        raise ValueError(f"ground truth is not an unambiguous yes/no: {ground_truth!r}")
    if pred is None:
        return EvaluationResult(
            decision="unscored",
            method="yes_no_canonical",
            normalized_prediction=None,
            normalized_ground_truth=truth,
            reason="prediction is not an unambiguous yes/no variant",
        )
    return EvaluationResult(
        decision="equivalent" if pred == truth else "not_equivalent",
        method="yes_no_canonical",
        normalized_prediction=pred,
        normalized_ground_truth=truth,
        reason="canonical yes/no comparison",
    )


# --------------------------------------------------------------------------- #
# C1: normalized short text + approved alias lists (never substring)
# --------------------------------------------------------------------------- #

_SURROUNDING_PUNCT_RE = re.compile(
    r'^[\s"\'“”‘’`*_~()\[\]{}<>]+|[\s"\'“”‘’`*_~()\[\]{}<>.,;:!?]+$'
)
_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")


def normalize_text(text: Optional[str]) -> Optional[str]:
    """Normalize short text: NFKC, casefold, collapse whitespace, strip
    surrounding punctuation, and drop one leading article."""
    if text is None:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    normalized = " ".join(normalized.strip().split())
    normalized = _SURROUNDING_PUNCT_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    normalized = _ARTICLE_RE.sub("", normalized.casefold())
    return normalized or None


class AliasList(BaseModel):
    """Task-specific approved aliases for one ground-truth answer.

    Aliases are an explicit, reviewable allowlist (plan.md §C1). Matching is
    still normalized exact equality against each entry — never substring
    containment.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    ground_truth: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    approved_by: str = Field(min_length=1)
    approved_on: str = Field(min_length=1)
    notes: Optional[str] = None

    def canonical_forms(self) -> dict[str, str]:
        """Map normalized surface form -> the alias entry it came from."""
        forms: dict[str, str] = {}
        canonical = normalize_text(self.ground_truth)
        if canonical:
            forms[canonical] = self.ground_truth
        for alias in self.aliases:
            normalized = normalize_text(alias)
            if not normalized:
                raise ValueError(f"alias normalizes to empty: {alias!r}")
            forms[normalized] = alias
        return forms


def load_alias_list(path: str | Path) -> dict[str, AliasList]:
    """Load approved alias lists from a JSON file keyed by task id.

    The file must contain a JSON object mapping task id -> AliasList fields.
    Every entry is validated; malformed files raise rather than being
    partially applied.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("alias file must be a JSON object keyed by task id")
    lists: dict[str, AliasList] = {}
    for task_id, fields in payload.items():
        entry = AliasList(task_id=task_id, **fields)
        lists[task_id] = entry
    return lists


def short_text_equivalent(
    prediction: Optional[str],
    ground_truth: str,
    *,
    aliases: Optional[AliasList] = None,
    numeric_fallback: bool = True,
) -> EvaluationResult:
    """Normalized exact match plus the task's approved alias list.

    Order: normalized exact -> approved alias exact -> numeric fallback (only
    when both sides parse numerically) -> unscored/not_equivalent. Short
    near-misses are ``unscored`` (judge candidates); longer mismatches are
    deterministic ``not_equivalent``.
    """
    pred = normalize_text(prediction)
    truth = normalize_text(ground_truth)
    if truth is None:
        raise ValueError("ground truth normalizes to empty")
    if pred is None:
        return EvaluationResult(
            decision="not_equivalent",
            method="normalized_text_exact",
            normalized_prediction=None,
            normalized_ground_truth=truth,
            reason="prediction is empty after normalization",
        )
    if pred == truth:
        return EvaluationResult(
            decision="equivalent",
            method="normalized_text_exact",
            normalized_prediction=pred,
            normalized_ground_truth=truth,
            reason="normalized exact match",
        )
    if aliases is not None:
        forms = aliases.canonical_forms()
        if pred in forms:
            return EvaluationResult(
                decision="equivalent",
                method="alias_exact",
                normalized_prediction=pred,
                normalized_ground_truth=truth,
                reason="matched approved alias",
                alias_matched=forms[pred],
            )
    if numeric_fallback:
        pred_num = parse_numeric(prediction)
        truth_num = parse_numeric(ground_truth)
        if pred_num is not None and truth_num is not None:
            result = numeric_equivalent(prediction, ground_truth)
            return EvaluationResult(
                decision=result.decision,
                method="numeric_fallback",
                normalized_prediction=result.normalized_prediction,
                normalized_ground_truth=result.normalized_ground_truth,
                reason=result.reason,
            )
    if len(pred.split()) <= UNSCORED_TEXT_MAX_WORDS:
        return EvaluationResult(
            decision="unscored",
            method="normalized_text_exact",
            normalized_prediction=pred,
            normalized_ground_truth=truth,
            reason="short non-exact answer requires the validated LLM judge",
        )
    return EvaluationResult(
        decision="not_equivalent",
        method="normalized_text_exact",
        normalized_prediction=pred,
        normalized_ground_truth=truth,
        reason="normalized exact mismatch",
    )


# --------------------------------------------------------------------------- #
# C1: dispatcher
# --------------------------------------------------------------------------- #


def evaluate_answer(
    final_answer: Optional[str],
    ground_truth: str,
    answer_format: str,
    *,
    aliases: Optional[AliasList] = None,
    tolerance: Optional[Decimal] = None,
) -> EvaluationResult:
    """Compare a parsed final answer to ground truth by ``answer_format``.

    ``answer_format`` is the task-schema field: ``number``, ``yes_no``, or
    ``short_text``. Judge-required outcomes come back ``unscored``.
    """
    if answer_format == "number":
        return numeric_equivalent(final_answer, ground_truth, tolerance=tolerance)
    if answer_format == "yes_no":
        return yes_no_equivalent(final_answer, ground_truth)
    if answer_format == "short_text":
        return short_text_equivalent(final_answer, ground_truth, aliases=aliases)
    raise ValueError(f"unknown answer_format: {answer_format!r}")


# --------------------------------------------------------------------------- #
# C1 scaffold: configured, validated LLM equivalence judge (disabled by default)
# --------------------------------------------------------------------------- #

JUDGE_REPLY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "equivalent": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": ["equivalent", "confidence", "rationale"],
    "additionalProperties": False,
}


class JudgeReply(BaseModel):
    """Validated judge reply (versioned rubric output schema, plan.md §C1)."""

    model_config = ConfigDict(extra="forbid")

    equivalent: bool
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=500)


class JudgeConfig(BaseModel):
    """Versioned, provenance-tracked judge configuration.

    Fail-closed: the judge only runs when ``enabled`` is true AND
    ``provenance.status == "approved"`` (i.e. the C3 validation report has
    been written and the researcher approved the accuracy threshold and tie
    policy). Everything else raises.
    """

    model_config = ConfigDict(extra="forbid")

    judge_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    rubric_version: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    reply_schema: dict[str, Any] = Field(default_factory=lambda: dict(JUDGE_REPLY_JSON_SCHEMA))
    provider: str
    model: str
    provider_route: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = Field(default=512, ge=1)
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    enabled: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt_template")
    @classmethod
    def _template_has_placeholders(cls, v: str) -> str:
        for placeholder in ("{answer_a}", "{answer_b}"):
            if placeholder not in v:
                raise ValueError(f"judge prompt_template must contain {placeholder}")
        for banned in ("{model}", "{provider}", "{condition}", "{cue}", "{expected}"):
            if banned in v:
                raise ValueError(
                    f"judge prompt_template must not expose {banned} (blinding, plan.md §C1)"
                )
        return v

    @model_validator(mode="after")
    def _enabled_requires_approval(self) -> "JudgeConfig":
        if self.enabled and self.provenance.get("status") != "approved":
            raise ValueError(
                "judge cannot be enabled until C3 validation is approved "
                "(provenance.status must be 'approved')"
            )
        return self

    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt_template.encode("utf-8")).hexdigest()

    def render_prompt(self, answer_a: str, answer_b: str) -> str:
        """Render the blinded judge prompt. Only the two answers are exposed —
        never model identity, condition, cue, or expected direction (§C1).

        Only ``{answer_a}``/``{answer_b}`` placeholders are substituted, so
        literal JSON braces in the template are left untouched.
        """
        return (
            self.prompt_template.replace("{answer_a}", answer_a.strip())
            .replace("{answer_b}", answer_b.strip())
        )


def load_judge_config(path: str | Path) -> JudgeConfig:
    """Load and validate a judge config from JSON. Unknown fields raise."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("judge config must be a JSON object")
    return JudgeConfig(**payload)


def judge_equivalence(
    answer_a: str,
    answer_b: str,
    *,
    config: JudgeConfig,
    client: Any,
    task_id: Optional[str] = None,
    run_id: Optional[str] = None,
    judgments_path: Optional[str | Path] = None,
) -> EvaluationResult:
    """Compare two answers with the configured LLM judge.

    This is a separate configured experiment (plan.md §C1): it routes through
    ``GenerationClient`` (so caching, cost, retry, and receipts all apply),
    validates the reply against the versioned JSON schema, and appends a
    ``judgment``-schema record (prompt, receipt, cache key, cost note) to
    ``judgments_path`` when provided. It must never be invoked during summary
    generation. Low-confidence replies and malformed replies are reported
    ``unscored`` rather than guessed.
    """
    # Imports are local so the deterministic path has no hard dependency on
    # the generation stack (and to avoid an import cycle).
    from src.generation import GenerationRequest

    if not config.enabled:
        raise RuntimeError(
            "LLM judge is not enabled. Enable only after C3 validation passes "
            "and the researcher approves the accuracy threshold and tie policy."
        )

    prompt = config.render_prompt(answer_a, answer_b)
    request = GenerationRequest(
        provider=config.provider,
        model=config.model,
        provider_route=config.provider_route,
        messages=[{"role": "user", "content": prompt}],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        prompt_template_version=f"judge-{config.judge_id}-rubric-{config.rubric_version}",
    )
    result = client.generate(request)

    reply: Optional[JudgeReply] = None
    parse_error: Optional[str] = None
    try:
        reply = JudgeReply.model_validate_json(result.text or "")
    except (ValidationError, ValueError) as exc:
        parse_error = str(exc)

    if reply is None:
        decision, method, reason = (
            "unscored",
            "llm_judge",
            f"judge reply failed schema validation: {parse_error}",
        )
    elif reply.confidence < config.confidence_threshold:
        decision, method, reason = (
            "unscored",
            "llm_judge",
            f"judge confidence {reply.confidence} below threshold {config.confidence_threshold}",
        )
    else:
        decision = "equivalent" if reply.equivalent else "not_equivalent"
        method, reason = "llm_judge", "judge decision above confidence threshold"

    evaluation = EvaluationResult(
        decision=decision,
        method=method,
        normalized_prediction=normalize_text(answer_a),
        normalized_ground_truth=normalize_text(answer_b),
        reason=reason,
        judge=reply,
    )

    if judgments_path is not None:
        record = schemas.stamp(
            "judgment",
            {
                "judge_id": config.judge_id,
                "rubric_version": config.rubric_version,
                "prompt_sha256": config.prompt_sha256(),
                "task_id": task_id,
                "run_id": run_id,
                "prompt": prompt,
                "judge_reply": reply.model_dump() if reply else None,
                "judge_parse_error": parse_error,
                "decision": evaluation.decision,
                "confidence_threshold": config.confidence_threshold,
                "cache_key": getattr(result, "cache_key", None),
                "cache_hit": getattr(result, "cache_hit", None),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "receipt": (
                    receipts_mod.sanitize_receipt(result.raw_response)
                    if isinstance(getattr(result, "raw_response", None), dict)
                    else None
                ),
            },
        )
        path = Path(judgments_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    return evaluation


def evaluate_with_llm(*_args: object, **_kwargs: object) -> dict[str, object]:
    """Reject unconfigured LLM judging.

    Canonical judge calls must be versioned, cached, costed, validated (C3),
    and routed through :class:`src.generation.GenerationClient` — use
    :func:`judge_equivalence` with an approved :class:`JudgeConfig`. The old
    direct SDK helper bypassed all of those controls.
    """
    raise RuntimeError(
        "LLM answer judging is not configured. Use deterministic scoring or "
        "the validated GenerationClient-based judge pipeline."
    )
