"""Offline tests for the C3 judge-validation runner (dev split, metrics)."""

import json

import pytest

from scripts.run_judge_validation import (
    confusion,
    frozen_identity_hash,
    load_pairs,
    rates,
    run_validation,
    write_report,
)
from src.evaluation import JudgeConfig
from src.generation import GenerationResult

_TEMPLATE = "A: {answer_a}\nB: {answer_b}\nJSON please"


def _judge_config(**overrides):
    fields = {
        "judge_id": "answer-equivalence",
        "rubric_version": "1.0.0",
        "prompt_template": _TEMPLATE,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "enabled": False,
    }
    fields.update(overrides)
    return JudgeConfig(**fields)


def _pair(pair_id, task_type, label, holdout, model_answer="x", ground_truth="y"):
    return {
        "pair_id": pair_id,
        "task_id": f"{task_type}-001",
        "task_type": task_type,
        "stratum": "ambiguous",
        "answer_format": "short_text",
        "ground_truth": ground_truth,
        "model_answer": model_answer,
        "deterministic_decision": "unscored",
        "holdout": holdout,
        "researcher_label": label,
        "researcher_notes": None,
    }


class _ScriptedClient:
    """Reply per pair_id order: list of judge JSON strings."""

    def __init__(self, replies):
        self._replies = list(replies)

    def generate(self, request):
        text = self._replies.pop(0)
        return GenerationResult(
            text=text,
            provider="deepseek",
            model="deepseek-v4-flash",
            timestamp="2026-07-22T00:00:00+00:00",
            cache_hit=False,
            cache_key="c" * 64,
            request={},
            raw_response={"id": "resp_1"},
        )


def test_frozen_identity_hash_ignores_labels():
    a = [_pair("p1", "math", "equivalent", False)]
    b = [_pair("p1", "math", "not_equivalent", False)]
    assert frozen_identity_hash(a) == frozen_identity_hash(b)
    c = [_pair("p1", "math", "equivalent", False, model_answer="different")]
    assert frozen_identity_hash(a) != frozen_identity_hash(c)


def test_confusion_and_rates():
    decided = [
        {"label": "equivalent", "judge": "equivalent"},
        {"label": "equivalent", "judge": "not_equivalent"},
        {"label": "not_equivalent", "judge": "not_equivalent"},
        {"label": "not_equivalent", "judge": "not_equivalent"},
    ]
    conf = confusion(decided)
    assert conf == {"tp": 1, "fn": 1, "tn": 2, "fp": 0}
    r = rates(conf)
    assert r["sensitivity"] == 0.5
    assert r["specificity"] == 1.0
    assert r["accuracy"] == 0.75


def test_run_validation_dev_only_with_abstain_and_ambiguous(tmp_path):
    pairs = [
        _pair("p1", "math", "equivalent", False),       # judged: agree
        _pair("p2", "math", "not_equivalent", False),   # judged: disagree
        _pair("p3", "factual", "ambiguous", False),     # excluded from denominators
        _pair("p4", "factual", "equivalent", False),    # judge abstains (bad JSON)
        _pair("p5", "math", "equivalent", True),        # holdout: never judged
    ]
    replies = [
        '{"equivalent": true, "confidence": 0.9, "rationale": "same"}',
        '{"equivalent": true, "confidence": 0.9, "rationale": "close"}',
        "garbage",
    ]
    report = run_validation(
        _judge_config(), pairs, _ScriptedClient(replies), judgments_path=tmp_path / "j.jsonl"
    )
    assert report["n_dev"] == 4
    assert report["n_ambiguous_label"] == 1
    assert report["n_abstain"] == 1
    assert report["confusion"] == {"tp": 1, "fn": 0, "tn": 0, "fp": 1}
    assert report["overall"]["sensitivity"] == 1.0
    assert report["overall"]["specificity"] == 0.0
    assert len(report["disagreements"]) == 1
    assert report["disagreements"][0]["pair_id"] == "p2"
    # Judgments recorded with the schema envelope.
    lines = (tmp_path / "j.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3  # ambiguous pair not judged; abstain still recorded
    record = json.loads(lines[0])
    assert record["schema"] == "judgment" and record["schema_version"] == 1


def test_write_report(tmp_path):
    report = {
        "judge_id": "answer-equivalence", "rubric_version": "1.0.0",
        "judge_model": "deepseek/deepseek-v4-flash", "confidence_threshold": 0.5,
        "prompt_sha256": "ab" * 32, "split": "dev",
        "n_dev": 4, "n_ambiguous_label": 1, "n_abstain": 1,
        "confusion": {"tp": 1, "fn": 0, "tn": 0, "fp": 1},
        "overall": {"sensitivity": 1.0, "specificity": 0.0, "accuracy": 0.5},
        "by_task_type": {"math": {"n": 2, "n_ambiguous_label": 0, "n_abstain": 0,
                                  "sensitivity": 1.0, "specificity": 0.0, "accuracy": 0.5}},
        "disagreements": [{"pair_id": "p2", "task_id": "math-001", "stratum": "ambiguous",
                           "label": "not_equivalent", "judge": "equivalent",
                           "judge_confidence": 0.9}],
    }
    paths = write_report(report, tmp_path)
    md = paths["md"].read_text()
    assert "Sensitivity: 1.000" in md and "p2" in md
    assert json.loads(paths["json"].read_text())["confusion"]["fp"] == 1
