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

PARSER_VERSION = "1.0.0"

# Anchored numbered-step markers: "1.", "2)", "Step 3:", "(4) -" etc. at line
# start (allowing leading whitespace and optional markdown bullet/quote chars).
_NUMBERED_MARKER_RE = re.compile(
    r"^[ \t]*(?:[-*•>][ \t]*)?(?:\*\*)?(?:step[ \t]+)?\(?(\d{1,3})\)?[.):\]]"
    r"(?:\*\*)?[ \t]+",
    re.IGNORECASE | re.MULTILINE,
)

# Final-answer line, anchored so we can locate its span (not just the answer).
_FINAL_ANSWER_LINE_RE = re.compile(
    r"^[ \t]*(?:\*\*)?final\s+answer\s*:", re.IGNORECASE | re.MULTILINE
)

# Blank-line paragraph boundary.
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")

# Sentence boundary: terminal punctuation followed by whitespace and a likely
# sentence start (capital, digit, quote, paren, bullet).
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*\-])")


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


def _parse_lines(reasoning: str) -> List[Tuple[int, int]]:
    """One step per non-blank hard-broken line."""
    spans: List[Tuple[int, int]] = []
    pos = 0
    for line in reasoning.splitlines(keepends=True):
        s, e = pos, pos + len(line)
        while s < e and reasoning[s].isspace():
            s += 1
        while e > s and reasoning[e - 1].isspace():
            e -= 1
        if s < e:
            spans.append((s, e))
        pos += len(line)
    return spans


def _parse_sentences(reasoning: str) -> List[Tuple[int, int]]:
    # Each match's whitespace belongs to the gap between sentences.
    matches = list(_SENTENCE_RE.finditer(reasoning))
    starts = [0] + [m.end() for m in matches]
    ends = [m.start() + 1 for m in matches] + [len(reasoning)]
    spans: List[Tuple[int, int]] = []
    for s, e in zip(starts, ends):
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
    if any(line.strip() for line in body.splitlines()[1:]):
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
        # Coarse strategies (numbered, paragraph) must not merge distinct
        # steps: any step containing a hard line break or an internal
        # sentence boundary fails the quality check so finer strategies run.
        # Line/sentence strategies are the fine-grained fallbacks themselves.
        if not errors and method in ("numbered", "paragraph"):
            merged = [
                i
                for i, (s, e) in enumerate(spans)
                if _step_merges_units(reasoning[s:e])
            ]
            if merged:
                errors = [f"step(s) {merged} merge multiple reasoning units"]
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
