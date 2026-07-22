"""Tests for `Final answer: X` parsing (src.evaluation.parse_final_answer).

This parser sits on the critical path for every downstream metric (answer-change
detection, outcome coding), so its edge cases are pinned down now.
"""

import pytest

from src.evaluation import evaluate_with_llm, parse_final_answer


@pytest.mark.parametrize(
    "text,expected",
    [
        # basic
        ("Step 1: 2+2=4.\nFinal answer: 4", "4"),
        # trailing whitespace is stripped
        ("Final answer: 42   ", "42"),
        ("Final answer: 42\t\n", "42"),
        # answer with units is preserved verbatim
        ("Final answer: 42 apples", "42 apples"),
        ("Final answer: 3.14 meters", "3.14 meters"),
        # forced yes/no stance (ethics)
        ("Reasoning...\nFinal answer: yes", "yes"),
        # light markdown emphasis is stripped
        ("**Final answer:** no", "no"),
        ("Final answer: **Tokyo**", "Tokyo"),
        # case-insensitive label
        ("final answer: cara", "cara"),
        ("FINAL ANSWER: 7", "7"),
        # leading whitespace on the line
        ("   Final answer: 12", "12"),
        # last occurrence wins if restated
        ("Final answer: 3\n...actually...\nFinal answer: 5", "5"),
    ],
)
def test_parse_final_answer_extracts(text, expected):
    assert parse_final_answer(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no final line here",
        "The answer is 4.",  # not the constrained format
        "",
        None,
        "Final answer:",  # present but empty
        "Final answer:    ",  # present but whitespace only
    ],
)
def test_parse_final_answer_returns_none(text):
    assert parse_final_answer(text) is None


def test_unvalidated_llm_judge_fails_closed():
    with pytest.raises(RuntimeError, match="not configured"):
        evaluate_with_llm("New York City", "NYC")
