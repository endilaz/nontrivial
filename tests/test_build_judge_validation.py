"""Offline tests for the C3 validation-set builder
(scripts/build_judge_validation.py). Deterministic, no model calls."""

import json

import pytest

from scripts.build_judge_validation import (
    HOLDOUT_FRACTION,
    MIN_PAIRS,
    build_pairs,
    difficulty_stratum,
    freeze_set,
)


def _record(task_id, task_type, category, final_answer, ground_truth, answer_format):
    return {
        "task_id": task_id,
        "task_type": task_type,
        "category": category,
        "final_answer": final_answer,
        "ground_truth": ground_truth,
        "answer_format": answer_format,
    }


def _sample_records():
    records = []
    # math: 1 clearly wrong, several exact (one '$'-prefixed, now exact)
    records.append(_record("math-001", "math", "incorrect", "5", "7", "number"))
    for i in range(2, 10):
        records.append(_record(f"math-{i:03d}", "math", "correct", str(i), str(i), "number"))
    records.append(_record("math-010", "math", "incorrect", "$660", "660", "number"))  # exact post-fix
    # logic: exact + one wrong
    for i in range(1, 8):
        records.append(_record(f"logic-{i:03d}", "logic", "correct", f"ans{i}", f"ans{i}", "short_text"))
    records.append(_record("logic-008", "logic", "incorrect", "1", "4", "short_text"))
    # factual: unscored/ambiguous + exact
    records.append(_record("factual-001", "factual", "unscored", "Francis Nethersole", "Sir Francis Nethersole", "short_text"))
    records.append(_record("factual-002", "factual", "unscored", "NYC", "New York City", "short_text"))
    for i in range(3, 9):
        records.append(_record(f"factual-{i:03d}", "factual", "correct", f"city{i}", f"city{i}", "short_text"))
    # ethics: exact yes/no + disagreements
    for i in range(1, 7):
        records.append(_record(f"ethics-{i:03d}", "ethics", "correct", "yes", "yes", "yes_no"))
    records.append(_record("ethics-007", "ethics", "incorrect", "no", "yes", "yes_no"))
    # unusable categories are skipped
    records.append(_record("math-900", "math", "parse_failure", None, "3", "number"))
    records.append(_record("math-901", "math", "truncation", "3", "3", "number"))
    return records


def test_difficulty_stratum_uses_current_evaluator():
    # '$'-prefixed answer stored as incorrect is an exact match under the
    # post-2026-07-22 parser.
    assert difficulty_stratum(_record("m", "math", "incorrect", "$660", "660", "number")) == "exact_match"
    assert difficulty_stratum(_record("m", "math", "incorrect", "5", "7", "number")) == "clearly_wrong"
    assert difficulty_stratum(_record("f", "factual", "unscored", "NYC", "New York City", "short_text")) == "ambiguous"


def test_build_pairs_stratified_and_deterministic():
    records = _sample_records()
    pairs_a = build_pairs(records, seed=1)
    pairs_b = build_pairs(records, seed=1)
    assert [p["pair_id"] for p in pairs_a] == [p["pair_id"] for p in pairs_b]
    assert pairs_a == pairs_b

    # All ambiguous and clearly-wrong cases are included exhaustively.
    ambiguous = [p for p in pairs_a if p["stratum"] == "ambiguous"]
    assert {p["task_id"] for p in ambiguous} == {"factual-001", "factual-002"}
    wrong = [p for p in pairs_a if p["stratum"] == "clearly_wrong"]
    assert {p["task_id"] for p in wrong} == {"math-001", "logic-008", "ethics-007"}

    # Skips unusable categories and covers all four task types.
    assert "math-900" not in {p["task_id"] for p in pairs_a}
    assert {p["task_type"] for p in pairs_a} == {"math", "logic", "factual", "ethics"}

    # Every pair starts unlabeled and both splits are populated.
    assert all(p["researcher_label"] is None for p in pairs_a)
    n_holdout = sum(1 for p in pairs_a if p["holdout"])
    assert 0 < n_holdout < len(pairs_a)


def test_freeze_set_writes_and_refuses_overwrite(tmp_path):
    pairs = build_pairs(_sample_records(), seed=1)
    out = freeze_set(pairs, tmp_path, seed=1, source=tmp_path / "responses.jsonl")
    manifest = json.loads(out["manifest"].read_text())
    assert manifest["frozen"] is True
    assert manifest["n_pairs"] == len(pairs)
    assert manifest["seed"] == 1
    assert len(manifest["validation_set_sha256"]) == 64
    assert "factual:ambiguous" in manifest["strata_counts"]
    # Labeling form lists every pair.
    form = out["form"].read_text()
    for pair in pairs:
        assert pair["pair_id"] in form
    with pytest.raises(FileExistsError, match="frozen"):
        freeze_set(pairs, tmp_path, seed=1, source=tmp_path / "responses.jsonl")


def test_pair_count_floor_enforced(tmp_path):
    # Fewer than MIN_PAIRS total pairs must abort (real set has ~40+; this
    # exercises the guard with a tiny input).
    few = _sample_records()[:5]
    pairs = build_pairs(few, seed=1)
    assert len(pairs) < MIN_PAIRS
