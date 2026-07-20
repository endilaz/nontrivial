import json
from pathlib import Path

from src.task_set import build_task_set


def test_build_task_set_writes_200_tasks(tmp_path):
    output_path = tmp_path / "task_set.jsonl"

    build_task_set(output_path=output_path, seed=7)

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 200

    by_type = {}
    for row in rows:
        by_type[row["task_type"]] = by_type.get(row["task_type"], 0) + 1
        assert row["id"]
        assert row["source"]
        assert row["answer_format"]
        assert "binary_ok" in row
        assert "perturbation_seed" in row

    assert by_type == {"ethics": 50, "logic": 50, "math": 50, "reasoning": 50}

    math_rows = [row for row in rows if row["task_type"] == "math"]
    assert any(row.get("perturbation_seed") is not None for row in math_rows)
    assert any(row.get("binary_formulation") for row in math_rows)
