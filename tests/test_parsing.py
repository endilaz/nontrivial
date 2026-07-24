"""Tests for src/parsing.py (Milestone D1 parser contracts)."""

from __future__ import annotations

import pytest

from src.parsing import (
    PARSER_VERSION,
    ParseResult,
    macro_average,
    parse_cot,
    score_boundaries,
    split_reasoning,
)


def _assert_spans_exact(result: ParseResult, reasoning: str) -> None:
    """Step spans must slice back to the step text and cover the reasoning."""
    assert result.ok, result.validation_errors
    assert result.parser_version == PARSER_VERSION
    prev_end = 0
    for step in result.steps:
        assert reasoning[step.start_char : step.end_char].strip() == step.text
        assert not reasoning[prev_end : step.start_char].strip()
        prev_end = step.end_char
    assert not reasoning[prev_end:].strip()
    assert [s.index for s in result.steps] == list(range(len(result.steps)))


# ---------------------------------------------------------------------------
# split_reasoning
# ---------------------------------------------------------------------------


class TestSplitReasoning:
    def test_reasoning_is_prefix_and_final_line_located(self) -> None:
        text = "Step one.\nStep two.\n\nFinal answer: 42\n"
        reasoning, span = split_reasoning(text)
        assert reasoning == "Step one.\nStep two."
        assert span is not None
        assert text[span[0] : span[1]] == "Final answer: 42"

    def test_uses_last_final_answer_line(self) -> None:
        text = "Final answer: wrong\nmore reasoning\nFinal answer: 7"
        reasoning, span = split_reasoning(text)
        assert reasoning == "Final answer: wrong\nmore reasoning"
        assert span is not None

    def test_no_final_line_returns_whole_text(self) -> None:
        reasoning, span = split_reasoning("just reasoning.  \n")
        assert reasoning == "just reasoning."
        assert span is None

    def test_case_insensitive_and_markdown(self) -> None:
        reasoning, span = split_reasoning("thinking\n**Final Answer:** yes")
        assert reasoning == "thinking"
        assert span is not None

    def test_nothing_dropped(self) -> None:
        text = "abc\nFinal answer: x"
        reasoning, span = split_reasoning(text)
        assert span is not None
        assert text[: span[0]].rstrip() == reasoning


# ---------------------------------------------------------------------------
# numbered strategy
# ---------------------------------------------------------------------------


class TestNumberedParsing:
    def test_basic_numbered_steps(self) -> None:
        text = "1. First step.\n2. Second step.\n3. Third step.\n\nFinal answer: 5"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert [s.method for s in result.steps] == ["numbered"] * 3
        assert result.steps[0].text.startswith("1.")
        assert len(result.steps) == 3

    def test_step_prefix_and_paren_styles(self) -> None:
        text = "Step 1: do a\nStep 2: do b\n\nFinal answer: a"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 2

        text2 = "1) alpha\n2) beta\n\nFinal answer: z"
        result2 = parse_cot(text2)
        _assert_spans_exact(result2, result2.reasoning_text)
        assert len(result2.steps) == 2
        assert result2.steps[0].method == "numbered"

    def test_preamble_becomes_leading_step(self) -> None:
        text = "Let me think about this.\n1. first\n2. second\n\nFinal answer: q"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 3
        assert result.steps[0].text == "Let me think about this."

    def test_multi_paragraph_last_step(self) -> None:
        # Numbered parse rejects the multi-paragraph final step (fail-closed
        # merge check); the continuation line becomes its own line-step.
        text = "1. first\n2. second part a\n\ncontinuation of two\n\nFinal answer: q"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 3
        assert all(s.method == "line" for s in result.steps)

    def test_single_marker_falls_back(self) -> None:
        # Only one marker: not numbered; each paragraph is one unit but the
        # first "paragraph" contains two hard lines -> line strategy wins.
        text = "1. lonely marker\n\nanother para\n\nFinal answer: q"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert result.steps[0].method == "paragraph"
        assert len(result.steps) == 2

    def test_non_sequential_numbering_falls_back(self) -> None:
        text = "1. first\n3. skipped two\n\nFinal answer: q"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert result.steps[0].method == "line"
        assert len(result.steps) == 2

    def test_decimals_not_treated_as_markers(self) -> None:
        # "3.5" must not be read as marker "3." — anchored marker requires
        # whitespace after the delimiter.
        text = "The value is 3.5 today.\nIt rose.\n\nFinal answer: q"
        result = parse_cot(text)
        assert all(s.method != "numbered" for s in result.steps)


# ---------------------------------------------------------------------------
# paragraph / sentence fallbacks
# ---------------------------------------------------------------------------


class TestFallbacks:
    def test_paragraph_segmentation(self) -> None:
        text = "First idea.\n\nSecond idea. More.\n\nThird.\n\nFinal answer: y"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert [s.method for s in result.steps] == ["paragraph"] * 3
        assert result.steps[1].text == "Second idea. More."

    def test_single_paragraph_sentence_split(self) -> None:
        # Guide §3: each sentence is one assertion -> one step each.
        text = "First sentence. Second sentence! Third?\n\nFinal answer: y"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 3
        assert all(s.method == "sentence" for s in result.steps)

    def test_one_sentence_whole_reasoning(self) -> None:
        text = "Only one sentence here.\n\nFinal answer: y"
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 1
        assert result.steps[0].text == "Only one sentence here."

    def test_deterministic_same_input_same_output(self) -> None:
        text = "1. a\n2. b\n\nFinal answer: c"
        r1, r2 = parse_cot(text), parse_cot(text)
        assert [(s.start_char, s.end_char, s.text) for s in r1.steps] == [
            (s.start_char, s.end_char, s.text) for s in r2.steps
        ]


# ---------------------------------------------------------------------------
# validation failures / fail-closed behavior
# ---------------------------------------------------------------------------


class TestValidation:
    def test_empty_text(self) -> None:
        result = parse_cot("")
        assert not result.ok
        assert result.steps == []

    def test_whitespace_only(self) -> None:
        result = parse_cot("   \n  ")
        assert not result.ok

    def test_no_reasoning_before_final_answer(self) -> None:
        result = parse_cot("Final answer: 42")
        assert not result.ok
        assert "empty reasoning" in result.validation_errors[0]

    def test_llm_fallback_used_and_validated(self, monkeypatch) -> None:
        import src.parsing as parsing_mod

        reasoning = "AAA\nBBB\nCCC"
        text = reasoning + "\n\nFinal answer: q"
        # Force every deterministic strategy to fail quality checks.
        bad = [(0, 0)]
        monkeypatch.setattr(parsing_mod, "_parse_paragraphs", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_lines", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_sentences", lambda _r: bad)
        spans = [(0, 3), (4, 7), (8, 11)]
        result = parse_cot(text, llm_splitter=lambda _r: spans)
        assert result.used_llm_fallback
        assert result.ok
        assert [s.text for s in result.steps] == ["AAA", "BBB", "CCC"]
        assert all(s.method == "llm" for s in result.steps)

    def test_llm_fallback_overlapping_spans_rejected(self, monkeypatch) -> None:
        import src.parsing as parsing_mod

        text = "AAA\nBBB\n\nFinal answer: q"
        bad = [(0, 0)]
        monkeypatch.setattr(parsing_mod, "_parse_paragraphs", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_lines", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_sentences", lambda _r: bad)
        result = parse_cot(text, llm_splitter=lambda _r: [(0, 5), (2, 7)])
        assert not result.ok
        assert result.steps == []

    def test_llm_fallback_non_covering_rejected(self, monkeypatch) -> None:
        import src.parsing as parsing_mod

        text = "AAA\nBBB\n\nFinal answer: q"
        bad = [(0, 0)]
        monkeypatch.setattr(parsing_mod, "_parse_paragraphs", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_lines", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_sentences", lambda _r: bad)
        result = parse_cot(text, llm_splitter=lambda _r: [(0, 3)])
        assert not result.ok

    def test_llm_fallback_empty_step_rejected(self, monkeypatch) -> None:
        import src.parsing as parsing_mod

        text = "AAA\nBBB\n\nFinal answer: q"
        bad = [(0, 0)]
        monkeypatch.setattr(parsing_mod, "_parse_paragraphs", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_lines", lambda _r: bad)
        monkeypatch.setattr(parsing_mod, "_parse_sentences", lambda _r: bad)
        result = parse_cot(text, llm_splitter=lambda _r: [(0, 0), (0, 7)])
        assert not result.ok

    def test_deterministic_success_never_calls_llm(self) -> None:
        def _boom(_r):
            raise AssertionError("llm splitter must not be called")

        text = "One. Two.\n\nFinal answer: q"
        result = parse_cot(text, llm_splitter=_boom)
        assert result.ok
        assert not result.used_llm_fallback

    def test_no_llm_splitter_no_llm_flag(self) -> None:
        text = "One. Two.\n\nFinal answer: q"
        result = parse_cot(text)
        assert not result.used_llm_fallback


# ---------------------------------------------------------------------------
# per-type fixtures mirroring real baseline formats
# ---------------------------------------------------------------------------


class TestRealisticFormats:
    def test_math_unnumbered_multiline(self) -> None:
        text = (
            "Mateo drives 80 miles in 2 hours, so his speed is  \n"
            "80 miles ÷ 2 hours = 40 miles per hour.  \n"
            "\n"
            "15 minutes is 15/60 = 0.25 hours.  \n"
            "Distance = speed × time = 40 × 0.25 = 10 miles.  \n"
            "\n"
            "The problem asks for a distance, so the answer is 10 miles.\n"
            "\n"
            "Final answer: 10 miles\n"
        )
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 5
        assert result.steps[0].method == "line"
        assert all(s.method == "line" for s in result.steps)

    def test_ethics_single_sentence(self) -> None:
        text = (
            "No, it is not morally acceptable, as it is a form of deception.  \n"
            "\n"
            "Final answer: No\n"
        )
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        assert len(result.steps) == 1

    def test_logic_quoted_clue(self) -> None:
        text = (
            'We are told directly in clue 12:  \n"The fish enthusiast lives '
            'in the second house."  \n\nSo the answer is house number 2.  \n'
            "\nFinal answer: 2\n"
        )
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        # Clue attribution filler merges with the quoted clue (guide v1.1.0
        # rule 1); quote + conclusion are separate sentence-steps.
        assert len(result.steps) == 2
        assert result.steps[0].text.startswith("We are told directly")
        text = (
            "Francis was born in 1587, and Elizabeth in 1596. Since 1587 is "
            "earlier, Francis was born first.\n\nFinal answer: Francis\n"
        )
        result = parse_cot(text)
        _assert_spans_exact(result, result.reasoning_text)
        # One paragraph containing two sentences -> two sentence-steps.
        assert len(result.steps) == 2
        assert all(s.method == "sentence" for s in result.steps)

    @pytest.mark.parametrize("task_run", ["amend2", "full"])
    def test_parses_real_baseline_cots(self, task_run: str) -> None:
        """Every real baseline CoT on disk must parse without errors."""
        import json
        from pathlib import Path

        path = Path(
            f"data/results/baseline_deepseek_v4_flash_v2_{task_run}/responses.jsonl"
        )
        if not path.exists():
            pytest.skip(f"{path} not present")
        failures = []
        for line in path.read_text().splitlines():
            row = json.loads(line)
            text = row.get("text") or ""
            if row.get("generation_error") or not text.strip():
                continue
            result = parse_cot(text)
            if not result.ok:
                failures.append((row["task_id"], result.validation_errors))
        assert failures == []


# ---------------------------------------------------------------------------
# D2 boundary F1 scorer (hand-calculated)
# ---------------------------------------------------------------------------


class TestBoundaryScoring:
    def test_perfect_match(self) -> None:
        reasoning = "aaa. bbb. ccc."
        # gold/predicted end_char of non-final steps: after "aaa." and "bbb."
        score = score_boundaries(reasoning, [4, 9], [4, 9])
        assert score.f1 == 1.0
        assert score.matched == 2

    def test_no_boundaries_either_side_is_perfect(self) -> None:
        score = score_boundaries("single step.", [], [])
        assert score.f1 == 1.0

    def test_oversegmenting_boundary_free_gold_is_zero(self) -> None:
        score = score_boundaries("single step.", [], [6])
        assert score.f1 == 0.0
        assert score.precision == 0.0
        assert score.recall == 0.0

    def test_undersegmenting_is_zero(self) -> None:
        score = score_boundaries("aaa. bbb.", [4], [])
        assert score.f1 == 0.0

    def test_whitespace_tolerance_matches(self) -> None:
        reasoning = "aaa. bbb. ccc."
        # predicted boundary 2 chars later, gap is ". " -> not whitespace-only
        # at offset 6 ("b") — use offset 5 (the space) and 6? Gap " " only.
        score = score_boundaries(reasoning, [4], [5])
        assert score.matched == 1
        assert score.f1 == 1.0

    def test_non_whitespace_gap_no_match(self) -> None:
        reasoning = "aaa. bbb."
        # predicted boundary at 6 (after "b"): gap " b" contains 'b'.
        score = score_boundaries(reasoning, [4], [6])
        assert score.matched == 0
        assert score.f1 == 0.0

    def test_partial_match_hand_calculated(self) -> None:
        reasoning = "aa. bb. cc. dd."
        gold = [3, 7, 11]
        pred = [3, 11]  # one correct, missed middle
        score = score_boundaries(reasoning, gold, pred)
        assert score.matched == 2
        assert score.precision == 1.0
        assert abs(score.recall - 2 / 3) < 1e-9
        assert abs(score.f1 - 0.8) < 1e-9

    def test_extra_predicted_boundary_lowers_precision(self) -> None:
        reasoning = "aa. bb. cc."
        gold = [3, 7]
        pred = [3, 5, 7]  # extra bogus boundary (offset 5 gap "b" -> no match)
        score = score_boundaries(reasoning, gold, pred)
        assert score.matched == 2
        assert abs(score.precision - 2 / 3) < 1e-9
        assert score.recall == 1.0
        assert abs(score.f1 - 0.8) < 1e-9

    def test_greedy_matching_is_one_to_one(self) -> None:
        reasoning = "aa. bb."
        gold = [3, 7]
        pred = [3, 3]  # duplicate predictions cannot both match gold[0]
        score = score_boundaries(reasoning, gold, pred)
        assert score.matched == 1

    def test_macro_average(self) -> None:
        s1 = score_boundaries("aa. bb.", [3], [3])
        s2 = score_boundaries("cc. dd.", [3], [])
        assert macro_average([s1, s2]) == 0.5

    def test_macro_average_empty_raises(self) -> None:
        import pytest as _pytest

        with _pytest.raises(ValueError):
            macro_average([])
