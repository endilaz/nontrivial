"""Tests for Milestone B7 review tooling and the acceptance gate."""

import json
from pathlib import Path

import pytest

from src.task_review import (
    ReviewError,
    export_review_form,
    load_decisions,
    merge_canonical,
)
from src.task_set import build_candidates


@pytest.fixture()
def workspace(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    output = tmp_path / "task_set.jsonl"
    manifest = tmp_path / "manifest.json"
    build_candidates(output_path=candidates)
    ids = [
        json.loads(line)["id"]
        for line in candidates.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "candidates": candidates,
        "decisions": decisions,
        "output": output,
        "manifest": manifest,
        "ids": ids,
    }


def _write_decisions(path: Path, ids, decision="approved"):
    with path.open("w", encoding="utf-8") as handle:
        for task_id in ids:
            handle.write(
                json.dumps(
                    {
                        "task_id": task_id,
                        "decision": decision,
                        "reviewer": "researcher",
                        "reviewed_at": "2026-07-21T00:00:00Z",
                        "notes": None,
                    }
                )
                + "\n"
            )


def test_merge_full_approval_passes_gate(workspace):
    _write_decisions(workspace["decisions"], workspace["ids"])
    merge_canonical(
        workspace["candidates"],
        workspace["decisions"],
        workspace["output"],
        workspace["manifest"],
    )
    from src.task_schema import load_task_set

    rows = load_task_set(workspace["output"])
    assert len(rows) == 200
    assert all(row.review_status == "approved" for row in rows)
    manifest = json.loads(workspace["manifest"].read_text(encoding="utf-8"))
    assert manifest["task_count"] == 200
    assert len(manifest["task_set_sha256"]) == 64
    assert manifest["review_decisions_sha256"]


def test_merge_fails_when_decisions_missing(workspace):
    _write_decisions(workspace["decisions"], workspace["ids"][:-1])
    with pytest.raises(ReviewError, match="lack review decisions"):
        merge_canonical(
            workspace["candidates"],
            workspace["decisions"],
            workspace["output"],
            workspace["manifest"],
        )
    assert not workspace["output"].exists()


def test_merge_fails_gate_when_quota_unmet(workspace):
    # Rejecting one task leaves 199 approved -> gate must fail.
    ids = workspace["ids"]
    _write_decisions(workspace["decisions"], ids)
    lines = workspace["decisions"].read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["decision"] = "rejected"
    lines[0] = json.dumps(first)
    workspace["decisions"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ReviewError, match="acceptance gate failed"):
        merge_canonical(
            workspace["candidates"],
            workspace["decisions"],
            workspace["output"],
            workspace["manifest"],
        )


def test_duplicate_decisions_rejected(workspace):
    ids = workspace["ids"]
    _write_decisions(workspace["decisions"], ids + [ids[0]])
    with pytest.raises(ReviewError, match="duplicate review decision"):
        load_decisions(workspace["decisions"])


def test_invalid_decision_value_rejected(workspace):
    with workspace["decisions"].open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "task_id": "math-001",
                    "decision": "maybe",
                    "reviewed_at": "2026-07-21T00:00:00Z",
                }
            )
            + "\n"
        )
    with pytest.raises(ReviewError, match="invalid decision"):
        load_decisions(workspace["decisions"])


def test_export_review_form(workspace, tmp_path):
    import csv

    form = tmp_path / "form.csv"
    count = export_review_form(workspace["candidates"], form)
    assert count == 200
    with form.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 201
    assert "check_ground_truth_follows" in rows[0]
    assert "decision" in rows[0]
