"""Phase 2 CoT parsing (Milestone D1): split free-form reasoning into steps.

Contract (Part II §8 D1):

- Separate the visible reasoning from the ``Final answer:`` line without
  dropping text. The reasoning is always a *prefix* of the response, so step
  ``start_char``/``end_char`` offsets are valid into both the original
  response text and ``ParseResult.reasoning_text``.
- Parse explicit numbered steps first (anchored line patterns).
- If numbering is absent or malformed, segment paragraphs, then sentences,
  deterministically.
- An LLM splitter may only be used when deterministic parsing fails quality
  checks; it must be supplied by the caller (routed through
  ``GenerationClient``) and return source character offsets. This module
  never makes model calls itself.
- Spans must be ordered, non-overlapping, non-empty, and covering (only
  whitespace may fall between steps). Violations are reported in
  ``validation_errors``; parsing fails closed (no fabricated steps).

The parser is deterministic and offline; same input + same ``PARSER_VERSION``
always yields the same steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

PARSER_VERSION = "1.2.0"

# Anchored numbered-step markers: "1.", "2)", "Step 3:", "(4) -" etc. at line
# start (allowing leading whitespace and optional markdown bullet/quote chars).
_NUMBERED_MARKER_RE = re.compile(
    r"^[ \t]*(?:[-*•>][ \t]*)?(?:\*\*)?(?:step[ \t]+)?\(?(\d{1,3})\)?[.):\]]"
    r"(?:\*\*)?[ \t]+",
    re.IGNORECASE | re.MULTILINE,
)

# Filler/setup lines merged into the following step (guide v1.1.0 §3 rules
# 1, 2, 5): meta text with no task assertion, puzzle-frame declarations, and
# case-analysis headers.
_FILLER_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"let(?:'|’)s\b.*|"                       # Let's break it down ...
    r"let me\b.*|"
    r"we have\b.*|"                           # We have two houses: ...
    r"from clues?:?[ \t]*|"                   # From clues:
    r"from clue \d+:[ \t]*$|"                 # From clue 1: (bare, no content)
    r"so:?[ \t]*|"                            # So:
    r"now (?:assign|fill|for)\b.*:?[ \t]*|"
    r"check(?:ing)?\b.*|"                     # Check all clues:
    r"case\b.*|"                              # Case: Red/White in (1,2):
    r"possible(?: pairs| positions)?:?[ \t]*|"
    r"now we have:?[ \t]*|"
    r"we are told directly[^\n]*:?[ \t]*"
    r")$",
    re.IGNORECASE,
)

# State-dump content lines: grid rows ("1: Blue", "House 2: fish enthusiast")
# and verification rows ("3: horses house 1 ✓"). A run of these (plus an
# optional intro line) merges into one step (guide v1.1.0 §3 rule 4).
_STATE_DUMP_LINE_RE = re.compile(
    r"^[ \t]*(?:house[ \t]+)?\d+\s*:.+$", re.IGNORECASE
)


def _line_texts(reasoning: str) -> List[Tuple[int, int]]:
    """Trimmed non-blank line spans."""
    out: List[Tuple[int, int]] = []
    pos = 0
    for line in reasoning.splitlines(keepends=True):
        s, e = pos, pos + len(line)
        pos = e
        while s < e and reasoning[s].isspace():
            s += 1
        while e > s and reasoning[e - 1].isspace():
            e -= 1
        if s < e:
            out.append((s, e))
    return out


def _merge_line_spans(
    reasoning: str, lines: List[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Merge filler/setup lines forward and state-dump runs into one span."""
    spans: List[Tuple[int, int]] = []
    n = len(lines)
    i = 0
    pending_start: Optional[int] = None  # start of absorbed filler lines
    while i < n:
        s, e = lines[i]
        text = reasoning[s:e]
        if _FILLER_LINE_RE.match(text):
            if pending_start is None:
                pending_start = s
            i += 1
            continue
        if _STATE_DUMP_LINE_RE.match(text):
            start = pending_start if pending_start is not None else s
            pending_start = None
            j = i
            while j < n and _STATE_DUMP_LINE_RE.match(
                reasoning[lines[j][0] : lines[j][1]]
            ):
                j += 1
            spans.append((start, lines[j - 1][1]))
            i = j
            continue
        start = pending_start if pending_start is not None else s
        pending_start = None
        spans.append((start, e))
        i += 1
    if pending_start is not None:
        # Trailing filler: attach to previous step if any, else its own.
        if spans:
            spans[-1] = (spans[-1][0], lines[-1][1])
        else:
            spans.append((pending_start, lines[-1][1]))
    return spans
_FINAL_ANSWER_LINE_RE = re.compile(
    r"^[ \t]*(?:\*\*)?final\s+answer\s*:", re.IGNORECASE | re.MULTILINE
)

# Blank-line paragraph boundary.
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")

# Sentence boundary: terminal punctuation (optionally followed by a closing
# quote/paren) followed by whitespace and a likely sentence start (capital,
# digit, paren, bullet). Quotes are excluded from starts so an attribution +
# quoted text stays one step (guide: clue attribution + quoted clue belongs
# to the same step). The match consumes the punctuation and whitespace;
# `m.start() + 1` is the sentence-end offset and `m.end()` the next start.
_SENTENCE_RE = re.compile(r"[.!?][\"')\]]?\s+(?=[A-Z0-9(\[*\-])")


@dataclass(frozen=True)
class ParsedStep:
    """One reasoning step with exact source span."""

    index: int
    text: str
    start_char: int
    end_char: int
    method: str  # "numbered" | "paragraph" | "sentence" | "llm"


@dataclass
class ParseResult:
    """Result of parsing one response's visible reasoning."""

    reasoning_text: str
    steps: List[ParsedStep] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    used_llm_fallback: bool = False
    validation_errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.validation_errors


# Type of a caller-supplied LLM splitter: takes reasoning text, returns
# (start, end) spans. Must be routed through GenerationClient by the caller.
LlmSplitter = Callable[[str], Sequence[Tuple[int, int]]]


def split_reasoning(text: str) -> Tuple[str, Optional[Tuple[int, int]]]:
    """Split response text into (reasoning, final-answer-line span).

    The reasoning is the text before the *last* ``Final answer:`` line
    (trailing whitespace excluded). Returns the reasoning text and the
    (start, end) span of the final-answer line in the original text, or
    ``None`` if there is no final-answer line.
    """
    matches = list(_FINAL_ANSWER_LINE_RE.finditer(text))
    if not matches:
        return text.rstrip(), None
    line_start = matches[-1].start()
    # Extend to end of line for the span.
    nl = text.find("\n", line_start)
    line_end = len(text) if nl == -1 else nl
    reasoning = text[:line_start].rstrip()
    return reasoning, (line_start, line_end)


def _spans_cover(
    reasoning: str, spans: Sequence[Tuple[int, int]]
) -> List[str]:
    """Validate ordering, non-overlap, non-empty, whitespace-only gaps."""
    errors: List[str] = []
    prev_end = 0
    for i, (s, e) in enumerate(spans):
        if not (0 <= s < e <= len(reasoning)):
            errors.append(f"step {i}: invalid span ({s},{e})")
            continue
        if not reasoning[s:e].strip():
            errors.append(f"step {i}: empty step")
        if s < prev_end:
            errors.append(f"step {i}: overlapping/reordered span")
        elif reasoning[prev_end:s].strip():
            errors.append(f"step {i}: non-covering gap before step")
        prev_end = max(prev_end, e)
    if spans and reasoning[spans[-1][1] :].strip():
        errors.append("non-covering trailing text after last step")
    if not spans and reasoning.strip():
        errors.append("no steps produced for non-empty reasoning")
    return errors


def _steps_from_spans(
    reasoning: str, spans: Sequence[Tuple[int, int]], method: str
) -> List[ParsedStep]:
    return [
        ParsedStep(
            index=i,
            text=reasoning[s:e].strip(),
            start_char=s,
            end_char=e,
            method=method,
        )
        for i, (s, e) in enumerate(spans)
    ]


def _parse_numbered(reasoning: str) -> Optional[List[Tuple[int, int]]]:
    """Numbered-step spans, or None if numbering absent/malformed.

    Requires >=2 markers with labels starting at 1 and strictly increasing
    by exactly 1 (1,2,3,...). A non-blank preamble before the first marker
    becomes its own leading step. Text after the last marker belongs to the
    last step.
    """
    markers = list(_NUMBERED_MARKER_RE.finditer(reasoning))
    if len(markers) < 2:
        return None
    labels = [int(m.group(1)) for m in markers]
    if labels[0] != 1 or any(b != a + 1 for a, b in zip(labels, labels[1:])):
        return None  # malformed numbering -> caller falls back
    spans: List[Tuple[int, int]] = []
    first_start = markers[0].start()
    if reasoning[:first_start].strip():
        # Preamble: from first non-whitespace char to first marker.
        s = len(reasoning) - len(reasoning.lstrip())
        spans.append((s, first_start))
    for i, m in enumerate(markers):
        start = m.start()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(reasoning)
        # Trim trailing whitespace of each step into the gap.
        while end > start and reasoning[end - 1].isspace():
            end -= 1
        spans.append((start, end))
    return spans


def _parse_paragraphs(reasoning: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    pos = 0
    for m in _PARAGRAPH_RE.finditer(reasoning):
        s, e = pos, m.start()
        if reasoning[s:e].strip():
            # Trim leading/trailing whitespace into gaps.
            while s < e and reasoning[s].isspace():
                s += 1
            while e > s and reasoning[e - 1].isspace():
                e -= 1
            spans.append((s, e))
        pos = m.end()
    s, e = pos, len(reasoning)
    if reasoning[s:e].strip():
        while s < e and reasoning[s].isspace():
            s += 1
        while e > s and reasoning[e - 1].isspace():
            e -= 1
        spans.append((s, e))
    return spans


# Bullet/list markers at line start: "- item", "* item", "Day 1: ..." style
# enumerated lines, and LaTeX "\( ..." display-math continuation lines.
_BULLET_RE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]+|[A-Za-z]+[ \t]+\d+\s*:|\\\()"
)


def _parse_lines(reasoning: str) -> List[Tuple[int, int]]:
    """One step per non-blank hard-broken line, merging bullet/list blocks.

    A contiguous run of bullet-item lines (``- item``) **within one
    paragraph**, plus a preceding one-line intro (e.g. "Total calories so
    far:") and any indented continuation lines, forms a single list step
    (guide §4: a givens list is one step). Filler/setup lines and
    state-dump runs are pre-merged per guide v1.1.0 §3.
    """
    reasoning_lines = _line_texts(reasoning)
    merged = _merge_line_spans(reasoning, reasoning_lines)
    # Re-run bullet merging on the merged spans, per paragraph.
    para_breaks = {m.start() for m in _PARAGRAPH_RE.finditer(reasoning)}
    spans: List[Tuple[int, int]] = []
    group: List[Tuple[int, int]] = []

    def _flush() -> None:
        nonlocal group
        if not group:
            return
        n = len(group)

        def _is_bullet(j: int) -> bool:
            return bool(_BULLET_RE.match(reasoning[group[j][0] : group[j][1]]))

        i = 0
        while i < n:
            if _is_bullet(i):
                j = i
                while j < n and _is_bullet(j):
                    j += 1
                spans.append((group[i][0], group[j - 1][1]))
                i = j
            elif i + 1 < n and _is_bullet(i + 1):
                j = i + 1
                while j < n and _is_bullet(j):
                    j += 1
                spans.append((group[i][0], group[j - 1][1]))
                i = j
            else:
                spans.append(group[i])
                i += 1
        group = []

    prev_end: Optional[int] = None
    for s, e in merged:
        if prev_end is not None and any(
            prev_end <= b < s for b in para_breaks
        ):
            _flush()
        group.append((s, e))
        prev_end = e
    _flush()
    return spans


def _is_indented_continuation(
    reasoning: str, lines: List[Tuple[int, int]], j: int
) -> bool:
    """Line j continues the previous bullet item (indented, not a bullet)."""
    s = lines[j][0]
    # original line started with spaces beyond the trimmed start?
    line_start = reasoning.rfind("\n", 0, s) + 1
    return line_start < s and not _BULLET_RE.match(reasoning[s : lines[j][1]])


def _parse_sentences(reasoning: str) -> List[Tuple[int, int]]:
    # The regex consumes terminal punctuation, an optional closing quote, and
    # the gap whitespace; the next sentence starts at the whitespace's end.
    matches = list(_SENTENCE_RE.finditer(reasoning))
    spans: List[Tuple[int, int]] = []
    prev_end = 0
    for m in matches:
        # Sentence ends right after punctuation + optional closing quote,
        # i.e. at the start of this match's whitespace run.
        ws_start = m.start()
        while reasoning[ws_start] not in " \t\n":
            ws_start += 1
        s, e = prev_end, ws_start
        while s < e and reasoning[s].isspace():
            s += 1
        while e > s and reasoning[e - 1].isspace():
            e -= 1
        if s < e:
            spans.append((s, e))
        prev_end = m.end()
    s, e = prev_end, len(reasoning)
    while s < e and reasoning[s].isspace():
        s += 1
    while e > s and reasoning[e - 1].isspace():
        e -= 1
    if s < e:
        spans.append((s, e))
    return spans


def _step_merges_units(step_text: str) -> bool:
    """True if a step contains >1 non-blank hard-broken line."""
    return sum(1 for line in step_text.splitlines() if line.strip()) > 1


def _is_list_block(step_text: str) -> bool:
    """True if a multi-line step is a bullet list block (guide §4: a givens
    list is one step): every non-blank line after the first is a bullet item
    or an indented continuation of one."""
    lines = [l for l in step_text.splitlines() if l.strip()]
    if len(lines) <= 1:
        return False
    if not _BULLET_RE.match(lines[1]):
        return False
    return all(
        _BULLET_RE.match(l) or (l[0] == " " and not _BULLET_RE.match(l))
        for l in lines[1:]
    )


def _degenerate_single_step(reasoning: str, spans: Sequence[Tuple[int, int]]) -> bool:
    """Quality check: one step that spans multiple hard-broken lines.

    A single-step parse of a reasoning block containing multiple sentences or
    hard-broken lines almost certainly merges distinct steps; finer-grained
    strategies (or the LLM fallback) should get a chance. A genuine
    one-sentence reasoning is fine.
    """
    if len(spans) != 1:
        return False
    s, e = spans[0]
    body = reasoning[s:e]
    if sum(1 for line in body.splitlines() if line.strip()) > 1:
        return True
    return len(_SENTENCE_RE.findall(body)) > 0


def parse_cot(
    text: str,
    llm_splitter: Optional[LlmSplitter] = None,
) -> ParseResult:
    """Parse a response's visible reasoning into discrete steps.

    Strategy order: numbered markers -> paragraphs -> sentences -> caller
    supplied LLM splitter. Fails closed: if no strategy yields valid spans,
    returns a result with ``validation_errors`` and no (or unvalidated) steps.
    """
    result = ParseResult(reasoning_text="")
    if not text or not text.strip():
        result.validation_errors.append("empty response text")
        return result

    reasoning, _final_span = split_reasoning(text)
    result.reasoning_text = reasoning
    if not reasoning.strip():
        result.validation_errors.append("empty reasoning (no visible CoT)")
        return result

    numbered_errors: List[str] = []
    for method, splitter in (
        ("numbered", _parse_numbered),
        ("paragraph", _parse_paragraphs),
        ("line", _parse_lines),
        ("sentence", _parse_sentences),
    ):
        spans = splitter(reasoning)
        if spans is None:
            continue
        errors = _spans_cover(reasoning, spans)
        # Coarse strategies (numbered, paragraph, line) must not merge
        # distinct steps: a step containing multiple sentences or hard lines
        # fails the quality check so finer strategies run. The sentence
        # strategy is the finest deterministic granularity and is exempt.
        if not errors and method in ("numbered", "paragraph", "line"):
            merged = [
                i
                for i, (s, e) in enumerate(spans)
                if _step_merges_units(reasoning[s:e])
                and not _is_list_block(reasoning[s:e])
            ]
            if merged:
                errors = [f"step(s) {merged} merge multiple reasoning units"]
        if not errors and _degenerate_single_step(reasoning, spans):
            # Fail closed: a degenerate single step is only acceptable from
            # the finest-grained deterministic strategy (sentences).
            if method != "sentence":
                errors = ["single step merges multiple units (degenerate parse)"]
        # Paragraph/line strategies are accepted only if they actually split
        # the reasoning; a single-step coarse parse means the reasoning is a
        # discursive block, which belongs to the sentence strategy.
        if not errors and method in ("paragraph", "line") and len(spans) == 1:
            errors = ["coarse strategy produced a single step (deferring)"]
        if not errors:
            result.steps = _steps_from_spans(reasoning, spans, method)
            return result
        # Numbered markers present but malformed: remember why; surfaced only
        # if every later strategy also fails.
        if method == "numbered":
            numbered_errors = [f"numbered parse rejected: {e}" for e in errors]

    # Deterministic strategies failed quality checks: LLM fallback only if
    # the caller supplied one (which must route through GenerationClient).
    if llm_splitter is not None:
        spans = list(llm_splitter(reasoning))
        errors = _spans_cover(reasoning, spans)
        result.used_llm_fallback = True
        if not errors:
            result.steps = _steps_from_spans(reasoning, spans, "llm")
            return result
        result.validation_errors.extend(
            f"llm parse rejected: {e}" for e in errors
        )

    if not result.steps:
        result.validation_errors.extend(numbered_errors)
        result.validation_errors.append(
            "all parse strategies failed quality checks"
        )
    return result


# ---------------------------------------------------------------------------
# D2/D3: boundary-level F1 scoring (docs/parser_annotation_guide.md §7)
# ---------------------------------------------------------------------------

#: Tolerance for trailing-whitespace placement only (guide §7).
BOUNDARY_TOLERANCE_CHARS = 2


@dataclass(frozen=True)
class BoundaryScore:
    """Boundary-level precision/recall/F1 for one CoT."""

    matched: int
    n_predicted: int
    n_gold: int
    precision: float
    recall: float
    f1: float


def _match_boundaries(
    reasoning: str,
    gold: Sequence[int],
    predicted: Sequence[int],
    tolerance: int = BOUNDARY_TOLERANCE_CHARS,
) -> int:
    """Greedy 1:1 matching; |p - g| <= tolerance with whitespace-only gap."""
    remaining = sorted(predicted)
    matched = 0
    for g in sorted(gold):
        best_i: Optional[int] = None
        for i, p in enumerate(remaining):
            if abs(p - g) <= tolerance and not reasoning[
                min(p, g) : max(p, g)
            ].strip():
                if best_i is None or abs(p - g) < abs(remaining[best_i] - g):
                    best_i = i
        if best_i is not None:
            remaining.pop(best_i)
            matched += 1
    return matched


def score_boundaries(
    reasoning: str,
    gold_boundaries: Sequence[int],
    predicted_boundaries: Sequence[int],
    tolerance: int = BOUNDARY_TOLERANCE_CHARS,
) -> BoundaryScore:
    """Score predicted step boundaries against gold (guide §7, frozen rule).

    Boundaries are ``end_char`` offsets of every non-final step. A CoT with no
    boundaries on either side scores F1 = 1; over/under-segmenting a
    boundary-free gold CoT scores F1 = 0.
    """
    n_gold, n_pred = len(gold_boundaries), len(predicted_boundaries)
    if n_gold == 0 and n_pred == 0:
        return BoundaryScore(0, 0, 0, 1.0, 1.0, 1.0)
    if n_gold == 0 or n_pred == 0:
        return BoundaryScore(0, n_pred, n_gold, 0.0, 0.0, 0.0)
    matched = _match_boundaries(
        reasoning, gold_boundaries, predicted_boundaries, tolerance
    )
    precision = matched / n_pred
    recall = matched / n_gold
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )
    return BoundaryScore(matched, n_pred, n_gold, precision, recall, f1)


def macro_average(scores: Sequence[BoundaryScore]) -> float:
    """Macro-averaged F1 across CoTs (guide §7: per-CoT then average)."""
    if not scores:
        raise ValueError("no scores to average")
    return sum(s.f1 for s in scores) / len(scores)
