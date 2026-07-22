"""Answer extraction / equivalence / outcome coding (CLAUDE.md Section 3).

Phase 0 implements only the piece the smoke test needs: extracting the single
constrained ``Final answer: X`` line that every task type must end with
(researcher decision, 2026-07-07). Equivalence judging and 3-way outcome coding
arrive in Phases 3-4; this module is where they will live.

The parser is intentionally permissive about surrounding whitespace and light
markdown emphasis (models sometimes bold the line), and it takes the LAST
``Final answer:`` occurrence so a restated answer wins. It returns ``None`` when
no such line exists, so downstream code can flag unparseable responses honestly
rather than silently coercing them.
"""

from __future__ import annotations

import re
from typing import Optional

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

def _build_llm_prompt(
    model_answer: str,
    reference_answer: str,
    context: str | None = None,
) -> str:
    """Return a prompt that asks the LLM to compare two answers.

    Parameters
    ----------
    model_answer:
        The answer produced by the model under evaluation.
    reference_answer:
        The ground‑truth or reference answer.
    context:
        Optional additional context that may help the LLM judge the answer.

    The prompt follows a simple instruction format that works well with
    chain‑of‑thought prompting.  The LLM is asked to output a JSON object
    with a ``score`` (0‑1) and a short ``explanation``.
    """

    prompt = [
        "You are a helpful assistant that evaluates whether two answers are equivalent. Keep in mind that answers may be expressed in different ways (e.g., synonyms, paraphrases, or pseudonyms), but they should be considered equivalent if they convey the same meaning.",
        "Answer 1 (model):\n" + model_answer.strip(),
        "Answer 2 (reference):\n" + reference_answer.strip(),
    ]
    if context:
        prompt.append("Context:\n" + context.strip())
    prompt.append(
        "Return exactly a JSON object with two keys: "
        "'score' (float 0‑1) indicating similarity, and "
        "'explanation' (short text). Do not add any other keys or additional formatting."
    )
    return "\n\n".join(prompt)

def evaluate_with_llm(*_args: object, **_kwargs: object) -> dict[str, object]:
    """Reject unconfigured LLM judging.

    Canonical judge calls must be versioned, cached, costed, and routed through
    :class:`src.generation.GenerationClient`. The old direct SDK helper bypassed
    all of those controls, so it remains unavailable until the Phase 1 judge
    validation pipeline is implemented.
    """
    raise RuntimeError(
        "LLM answer judging is not configured. Use deterministic scoring or "
        "the validated GenerationClient-based judge pipeline."
    )
