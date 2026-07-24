"""Tests for scripts/label_parser_boundaries.py (D3 labeling aid)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = PROJECT_ROOT / "data/validation/parser_sample_v1.jsonl"

from scripts.label_parser_boundaries import (  # noqa: E402
    _candidate_spans,
    _gold_boundaries,
    _labeled_windows,
    _parse_decisions,
)


class TestCandidateSpans:
    def test_cover_reasoning_modulo_whitespace(self) -> None:
        reasoning = "First line.  \nSecond line.\n\nNew para here."
        spans = _candidate_spans(reasoning)
        prev = 0
        for s, e in spans:
            assert not reasoning[prev:s].strip()
            assert reasoning[s:e].strip()
            prev = e
        assert not reasoning[prev:].strip()

    def test_multiline_paragraph_splits_by_line(self) -> None:
        reasoning = "line one\nline two\nline three"
        spans = _candidate_spans(reasoning)
        assert [reasoning[s:e] for s, e in spans] == [
            "line one",
            "line two",
            "line three",
        ]

    def test_single_line_paragraph_sentence_split(self) -> None:
        reasoning = "One sentence. Two sentences!\n\nPara two."
        spans = _candidate_spans(reasoning)
        texts = [reasoning[s:e] for s, e in spans]
        assert texts == ["One sentence.", "Two sentences!", "Para two."]

    def test_single_sentence_line_not_split(self) -> None:
        reasoning = "Just one sentence here."
        spans = _candidate_spans(reasoning)
        assert len(spans) == 1


class TestDecisionsParsing:
    def test_basic(self, tmp_path: Path) -> None:
        d = _parse_decisions("math-001: 1 2 3\nethics-002:\n# comment\n")
        assert d == {"math-001": [1, 2, 3], "ethics-002": []}

    def test_unsorted_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse_decisions("math-001: 3 1")

    def test_malformed_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _parse_decisions("not a decision line")


class TestGoldBoundaries:
    def test_offsets_from_candidate_numbers(self) -> None:
        reasoning = "aaa\nbbb\nccc"
        spans = _candidate_spans(reasoning)  # 3 candidates
        gold, region = _gold_boundaries(reasoning, spans, [0, 1], None, 40, 20)
        assert gold == [3, 7]
        assert region is None

    def test_final_candidate_not_a_boundary(self) -> None:
        reasoning = "aaa\nbbb"
        spans = _candidate_spans(reasoning)
        with pytest.raises(SystemExit):
            _gold_boundaries(reasoning, spans, [1], None, 40, 20)

    def test_long_cot_region(self) -> None:
        reasoning = "\n".join(f"line {i}" for i in range(100))
        spans = _candidate_spans(reasoning)
        windows = _labeled_windows(len(spans), 10, 5)
        assert windows is not None
        gold, region = _gold_boundaries(
            reasoning, spans, [3, 97], windows, 10, 5
        )
        assert region is not None
        head_end, tail_start = region
        assert gold[0] <= head_end
        assert gold[1] >= tail_start


@pytest.mark.skipif(not SAMPLE.exists(), reason="frozen sample not built")
class TestAgainstFrozenSample:
    def test_candidates_cover_every_sampled_cot(self) -> None:
        from src.parsing import split_reasoning

        for line in SAMPLE.read_text().splitlines():
            rec = json.loads(line)
            reasoning, _ = split_reasoning(rec["response_text"])
            spans = _candidate_spans(reasoning)
            prev = 0
            for s, e in spans:
                assert not reasoning[prev:s].strip(), rec["task_id"]
                prev = e
            assert not reasoning[prev:].strip(), rec["task_id"]

    def test_only_logic_005_is_long(self) -> None:
        from src.parsing import split_reasoning

        long_ids = []
        for line in SAMPLE.read_text().splitlines():
            rec = json.loads(line)
            reasoning, _ = split_reasoning(rec["response_text"])
            if len(reasoning) > 4000:
                long_ids.append(rec["task_id"])
        assert long_ids == ["logic-005"]
