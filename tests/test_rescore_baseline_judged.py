"""Offline tests for the judged baseline re-score (scripts/rescore_baseline_judged.py)."""

import json

import pytest

from scripts.rescore_baseline_judged import rescore_unscored
from src.generation import GenerationResult


def _record(task_id, task_type, category, final_answer="x", ground_truth="y"):
    return {
        "task_id": task_id,
        "condition_id": "baseline",
        "task_type": task_type,
        "answer_format": "short_text",
        "category": category,
        "final_answer": final_answer,
        "ground_truth": ground_truth,
    }


class _ScriptedClient:
    def __init__(self, replies):
        self._replies = list(replies)

    def generate(self, request):
        return GenerationResult(
            text=self._replies.pop(0),
            provider="deepseek",
            model="deepseek-v4-flash",
            timestamp="2026-07-22T00:00:00+00:00",
            cache_hit=False,
            cache_key="c" * 64,
            request={},
            raw_response={"id": "resp"},
        )


def _run_dir(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    records = [
        _record("factual-001", "factual", "unscored", "NYC", "New York City"),
        _record("factual-002", "factual", "unscored", "Am Rong", "filmmaker"),
        _record("factual-003", "factual", "correct", "Paris", "Paris"),
        _record("math-001", "math", "correct", "42", "42"),
        _record("ethics-001", "ethics", "incorrect", "no", "yes"),
    ]
    with (run_dir / "responses.jsonl").open("w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": "test_run"}))
    return run_dir


def _judge_config(tmp_path):
    config = {
        "judge_id": "answer-equivalence",
        "rubric_version": "1.0.0",
        "prompt_template": "A {answer_a} B {answer_b}",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "thinking_setting": {"thinking": {"type": "disabled"}},
        "enabled": True,
        "provenance": {"status": "approved"},
    }
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(config))
    return path


def test_rescore_resolves_unscored_and_preserves_originals(tmp_path):
    run_dir = _run_dir(tmp_path)
    original_responses = (run_dir / "responses.jsonl").read_bytes()
    client = _ScriptedClient([
        '{"equivalent": true, "confidence": 0.95, "rationale": "same city"}',
        '{"equivalent": false, "confidence": 0.9, "rationale": "different"}',
    ])
    addendum = rescore_unscored(
        run_dir, _judge_config(tmp_path), client, judgments_run_path=tmp_path / "j.jsonl"
    )
    assert addendum["n_judged"] == 2
    assert addendum["n_resolved_correct"] == 1
    assert addendum["n_resolved_incorrect"] == 1
    factual = addendum["per_task_type"]["factual"]
    assert factual["categories"]["correct"] == 2
    assert factual["categories"]["incorrect"] == 1
    assert factual["categories"]["unscored"] == 0
    assert factual["accuracy_excluding_unscored"] == 2 / 3
    # Ethics untouched and still source-label agreement.
    assert addendum["per_task_type"]["ethics"]["source_label_agreement"] == 0.0
    # Original responses untouched; new files written.
    assert (run_dir / "responses.jsonl").read_bytes() == original_responses
    resolutions = [json.loads(l) for l in (run_dir / "judgments_baseline.jsonl").open()]
    assert len(resolutions) == 2
    assert (run_dir / "summary_judged.json").exists()
    # Append-only: a second run refuses.
    with pytest.raises(FileExistsError, match="append-only"):
        rescore_unscored(run_dir, _judge_config(tmp_path), client, judgments_run_path=tmp_path / "j.jsonl")
