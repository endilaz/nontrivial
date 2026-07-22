"""Tests for the A2 preserve-and-invalidate operation (src/invalidation.py).

All tests build a throwaway repo tree under ``tmp_path`` — nothing touches the
real ``data/`` — and assert the operation moves exactly the invalid artifacts,
records a verifying SHA-256 manifest, preserves source datasets, and is
idempotent.
"""

import json

import pytest

from src import invalidation
from src.invalidation import (
    ALREADY,
    CONFLICT,
    MOVE,
    InvalidationConflictError,
    build_plan,
    execute,
    sha256_file,
)


def _make_repo(root):
    """Create a minimal fake repo with the invalid artifacts + source datasets."""
    (root / "data" / "tasks").mkdir(parents=True)
    (root / "data" / "results").mkdir(parents=True)
    (root / "data" / "cache").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)

    # Invalid generated artifacts.
    (root / "data" / "tasks" / "task_set.jsonl").write_text('{"id": "math-001"}\n', encoding="utf-8")
    (root / "data" / "results" / "baseline_deepseek_v4_flash_v1.baseline_generations.jsonl").write_text("x\n", encoding="utf-8")
    (root / "data" / "results" / "baseline_accuracy_summary.json").write_text("{}\n", encoding="utf-8")
    (root / "data" / "results" / "smoke_test.jsonl").write_text("s\n", encoding="utf-8")
    (root / "data" / "results" / "smoke_test.config.yaml").write_text("run_id: smoke\n", encoding="utf-8")
    (root / "data" / "cache" / "deadbeef.json").write_text('{"cached": true}\n', encoding="utf-8")
    (root / "logs" / "cost_log.csv").write_text("timestamp,run_id\n", encoding="utf-8")

    # Things that must be PRESERVED (source datasets + keep files).
    (root / "data" / "tasks" / "MATH_gsm8k").mkdir()
    (root / "data" / "tasks" / "MATH_gsm8k" / "train.jsonl").write_text("gsm8k\n", encoding="utf-8")
    (root / "data" / "results" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "data" / "raw").mkdir()
    (root / "data" / "raw" / ".gitkeep").write_text("", encoding="utf-8")


def test_dry_run_plans_moves_without_touching_disk(tmp_path):
    _make_repo(tmp_path)
    before = sha256_file(tmp_path / "data" / "tasks" / "task_set.jsonl")

    result = execute(tmp_path, dry_run=True)

    assert result.dry_run is True
    assert result.manifest_path is None
    # Every invalid artifact is planned as a MOVE.
    assert {a.original_path for a in result.moved} == {
        "data/tasks/task_set.jsonl",
        "data/results/baseline_deepseek_v4_flash_v1.baseline_generations.jsonl",
        "data/results/baseline_accuracy_summary.json",
        "data/results/smoke_test.jsonl",
        "data/results/smoke_test.config.yaml",
        "data/cache/deadbeef.json",
        "logs/cost_log.csv",
    }
    # Nothing actually moved.
    assert (tmp_path / "data" / "tasks" / "task_set.jsonl").is_file()
    assert not (tmp_path / "data" / "tasks" / "invalidated").exists()
    assert sha256_file(tmp_path / "data" / "tasks" / "task_set.jsonl") == before


def test_execute_moves_hashes_and_manifests(tmp_path):
    _make_repo(tmp_path)
    orig_hash = sha256_file(tmp_path / "data" / "results" / "smoke_test.jsonl")

    result = execute(tmp_path)

    dated = "invalidated/2026-07-21"
    # Originals gone, invalidated copies present.
    assert not (tmp_path / "data" / "tasks" / "task_set.jsonl").exists()
    moved = tmp_path / "data" / "tasks" / dated / "task_set.jsonl"
    assert moved.is_file()
    assert not (tmp_path / "data" / "results" / "smoke_test.jsonl").exists()
    assert (tmp_path / "data" / "results" / dated / "smoke_test.jsonl").is_file()
    assert not (tmp_path / "data" / "cache" / "deadbeef.json").exists()
    assert (tmp_path / "data" / "cache" / dated / "deadbeef.json").is_file()
    assert not (tmp_path / "logs" / "cost_log.csv").exists()
    assert (tmp_path / "logs" / dated / "cost_log.csv").is_file()

    # Manifest exists and every recorded hash verifies against the moved file.
    assert result.manifest_path.is_file()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "invalidation_manifest"
    assert manifest["invalidation_date"] == "2026-07-21"
    assert manifest["artifact_count"] == len(manifest["artifacts"]) == 7
    for entry in manifest["artifacts"]:
        dest = tmp_path / entry["invalidated_path"]
        assert dest.is_file(), entry["invalidated_path"]
        assert sha256_file(dest) == entry["sha256"]
        assert entry["reason_code"] and entry["reason"]
    # run_id derived for baseline/smoke, None for task set / cache / ledger.
    by_orig = {e["original_path"]: e for e in manifest["artifacts"]}
    assert by_orig["data/results/smoke_test.jsonl"]["run_id"] == "smoke_test"
    assert by_orig["data/tasks/task_set.jsonl"]["run_id"] is None
    # The moved smoke file is byte-preserved.
    assert sha256_file(tmp_path / "data" / "results" / dated / "smoke_test.jsonl") == orig_hash


def test_source_datasets_and_keepfiles_are_preserved(tmp_path):
    _make_repo(tmp_path)
    execute(tmp_path)

    # Downloaded source dataset untouched.
    ds = tmp_path / "data" / "tasks" / "MATH_gsm8k" / "train.jsonl"
    assert ds.is_file()
    assert ds.read_text(encoding="utf-8") == "gsm8k\n"
    # .gitkeep files are not baseline_/smoke_ and must remain.
    assert (tmp_path / "data" / "results" / ".gitkeep").is_file()
    assert (tmp_path / "data" / "raw" / ".gitkeep").is_file()


def test_idempotent_second_run_is_noop_with_identical_manifest(tmp_path):
    _make_repo(tmp_path)
    first = execute(tmp_path)
    manifest_text_1 = first.manifest_path.read_text(encoding="utf-8")

    second = execute(tmp_path)
    manifest_text_2 = second.manifest_path.read_text(encoding="utf-8")

    # Second run finds everything already invalidated, moves nothing.
    assert second.moved == []
    assert len(second.already) == 7
    # Manifest is byte-stable across runs.
    assert manifest_text_1 == manifest_text_2


def test_conflicting_copies_raise_before_moving(tmp_path):
    _make_repo(tmp_path)
    execute(tmp_path)  # first invalidation

    # Recreate an original that DIFFERS from the preserved copy → conflict.
    (tmp_path / "data" / "tasks" / "task_set.jsonl").write_text('{"id": "DIFFERENT"}\n', encoding="utf-8")

    plan = build_plan(tmp_path)
    conflict_actions = [a for a in plan if a.action == CONFLICT]
    assert [a.original_path for a in conflict_actions] == ["data/tasks/task_set.jsonl"]

    with pytest.raises(InvalidationConflictError):
        execute(tmp_path)
    # The differing original is left in place (nothing silently overwritten).
    assert (tmp_path / "data" / "tasks" / "task_set.jsonl").read_text(encoding="utf-8") == '{"id": "DIFFERENT"}\n'


def test_identical_leftover_original_is_deduped(tmp_path):
    _make_repo(tmp_path)
    execute(tmp_path)

    dated = "invalidated/2026-07-21"
    # Recreate an original byte-identical to the preserved copy.
    preserved = tmp_path / "logs" / dated / "cost_log.csv"
    (tmp_path / "logs" / "cost_log.csv").write_text(preserved.read_text(encoding="utf-8"), encoding="utf-8")

    plan = build_plan(tmp_path)
    ledger = [a for a in plan if a.category == "cost_ledger"][0]
    assert ledger.action == ALREADY and ledger.duplicate_original is True

    execute(tmp_path)
    # Leftover original removed; preserved copy still present.
    assert not (tmp_path / "logs" / "cost_log.csv").exists()
    assert preserved.is_file()


def test_empty_repo_is_a_clean_noop(tmp_path):
    # No artifacts present at all (e.g. cache empty): plan is empty, no error.
    (tmp_path / "data" / "tasks").mkdir(parents=True)
    result = execute(tmp_path)
    assert result.actions == []
    assert result.manifest["artifact_count"] == 0
    assert result.manifest_path.is_file()
