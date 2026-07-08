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
