"""Tests for scripts/build_parser_validation_sample.py (D3 sample builder)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

sys_path_hint = Path(__file__).resolve().parent.parent

from scripts.build_parser_validation_sample import (  # noqa: E402
    ANNOTATION_PATH,
    SAMPLE_PATH,
    _annotation_template,
    build_sample,
)

PROJECT_ROOT = sys_path_hint


@pytest.mark.skipif(
    not (PROJECT_ROOT / "data/results/baseline_deepseek_v4_flash_v2_full").exists(),
    reason="baseline results not present",
)
class TestSampleBuilder:
    def test_sample_composition_and_determinism(self) -> None:
        s1 = build_sample()
        s2 = build_sample()
        ids1 = [r["task_id"] for r in s1]
        ids2 = [r["task_id"] for r in s2]
        assert ids1 == ids2  # seeded, deterministic
        assert len(s1) == 32
        for t in ("math", "logic", "factual", "ethics"):
            assert sum(1 for r in s1 if r["task_type"] == t) == 8
        # No math rows from the superseded `full` run.
        assert not [
            r
            for r in s1
            if r["task_type"] == "math"
            and r["source_run_id"] == "baseline_deepseek_v4_flash_v2_full"
        ]

    def test_frozen_sample_matches_rebuild(self) -> None:
        sample_path = PROJECT_ROOT / SAMPLE_PATH
        annotation_path = PROJECT_ROOT / ANNOTATION_PATH
        if not sample_path.exists():
            pytest.skip("frozen sample not built yet")
        frozen = [json.loads(l) for l in sample_path.read_text().splitlines()]
        assert len(frozen) == 32
        # Frozen task ids match a fresh deterministic rebuild.
        rebuilt_ids = sorted(r["task_id"] for r in build_sample())
        assert sorted(r["task_id"] for r in frozen) == rebuilt_ids
        # Every record parsed ok and carries required provenance.
        for rec in frozen:
            assert rec["parse"]["ok"]
            assert rec["schema"] == "parser_validation_sample"
            assert len(rec["reasoning_sha256"]) == 64
            assert rec["response_key"][0] == rec["task_id"]
        # Annotation template aligns 1:1 with the sample.
        annotations = [
            json.loads(l) for l in annotation_path.read_text().splitlines()
        ]
        assert [a["task_id"] for a in annotations] == [
            r["task_id"] for r in frozen
        ]
        for a in annotations:
            assert a["schema"] == "cot_boundary_annotation"
            assert a["guide_version"] == "1.0.0"

    def test_annotation_template_shape(self) -> None:
        rec = {
            "task_id": "math-999",
            "task_type": "math",
            "source_run_id": "run",
            "response_key": ["math-999", "baseline", "instruction_only", 0],
        }
        a = _annotation_template(rec)
        assert a["boundaries"] == []
        assert a["adjudicated"] is False
        assert a["annotator"] is None
