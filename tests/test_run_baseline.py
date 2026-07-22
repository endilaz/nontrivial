"""Offline integration tests for the C2 baseline runner (scripts/run_baseline.py).

Uses a fake client and temp dirs to prove the wiring: schema-validated tasks ->
task-set hash verification -> capability gate -> estimate + reservation ->
append-only RunDirectory + journal -> sanitized receipts -> C1 deterministic
scoring with unscored (never silent judging) -> reconciliation -> summary with
the six C2 categories. No network, no real budget files.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.run_baseline import (
    CATEGORIES,
    categorize,
    load_tasks,
    run_baseline,
    select_dry_run_pilot,
    verify_task_set_hash,
)
from src.budget_state import approve_historical_spend, load_state
from src.capabilities import matrix_to_dict, CapabilityProbe
from src.generation import GenerationResult


# --------------------------------------------------------------------------- #
# Fixtures: a synthetic but schema-valid task set + config + capability matrix
# --------------------------------------------------------------------------- #


def _base_task(task_id, task_type, ground_truth):
    return {
        "schema": "task_record",
        "schema_version": 1,
        "id": task_id,
        "task_type": task_type,
        "problem": f"Problem for {task_id}",
        "source": "synthetic",
        "source_id": f"src-{task_id}",
        "source_split": "test",
        "source_license": "MIT",
        "ground_truth": ground_truth,
        "binary_ok": False,
        "direction2_probe": False,
        "review_status": "approved",
    }


def _math(task_id, answer):
    return {
        **_base_task(task_id, "math", answer),
        "answer_format": "number",
        "scoring_policy": "numeric",
        "math": {
            "original_problem": "p",
            "original_answer": answer,
            "source_row": 0,
            "transformations": [],
            "transformation_seed": 0,
            "difficulty_proxy": "solution_step_count",
            "difficulty_bin": 1,
        },
    }


def _logic(task_id, answer):
    return {
        **_base_task(task_id, "logic", answer),
        "answer_format": "short_text",
        "scoring_policy": "normalized_text",
        "logic": {
            "source_example_index": 0,
            "input_sha256": "a" * 64,
            "solver_name": "z3",
            "solver_version": "4.12",
            "unique_solution_proof": "checked",
        },
    }


def _factual(task_id, answer):
    return {
        **_base_task(task_id, "factual", answer),
        "answer_format": "short_text",
        "scoring_policy": "normalized_text",
        "factual": {
            "context": [
                {"title": "Alpha", "sentences": ["s0", "s1"]},
                {"title": "Beta", "sentences": ["t0"]},
            ],
            "supporting_facts": [
                {"title": "Alpha", "sentence_index": 0},
                {"title": "Beta", "sentence_index": 0},
            ],
            "answer_aliases": [],
            "hotpot_type": "bridge",
            "hotpot_level": "easy",
            "tokenizer_revision": "r1",
            "context_token_count": 10,
        },
    }


def _ethics(task_id, answer):
    return {
        **_base_task(task_id, "ethics", answer),
        "answer_format": "yes_no",
        "scoring_policy": "source_label_agreement",
        "ethics": {
            "raw_text": "scenario",
            "raw_label": 1,
            "source_file": "test.csv",
            "source_row": 0,
            "label_mapping_version": "v1",
        },
    }


ALL_TASKS = [
    _math("math-001", "42"),
    _math("math-002", "7"),
    _logic("logic-001", "alice"),
    _factual("factual-001", "Paris"),
    _ethics("ethics-001", "yes"),
]


def _write_task_set(tmp_path: Path, tasks=ALL_TASKS) -> Path:
    tasks_path = tmp_path / "task_set.jsonl"
    with tasks_path.open("w", encoding="utf-8") as fh:
        for task in tasks:
            fh.write(json.dumps(task) + "\n")
    manifest = {
        "task_set_sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
    }
    (tmp_path / "task_set_manifest.json").write_text(json.dumps(manifest))
    return tasks_path


def _write_config(tmp_path: Path) -> Path:
    config = {
        "run_id": "baseline_test_v1",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "temperature": 0.0,
        "max_tokens": 512,
        "thinking_setting": {"thinking": {"type": "disabled"}},
        "seed": 0,
        "prompt_template": "Solve:\n\n{problem}",
        "prompt_template_version": "v1",
        "date": "2026-07-22",
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def _write_capabilities(tmp_path: Path) -> Path:
    probe = CapabilityProbe(
        probe_id="deepseek_nonthinking_zero_reasoning",
        provider="deepseek",
        model="deepseek-v4-flash",
        capability="thinking_disabled",
        status="pass",
        verified_on="2026-07-22",
        expires_on="2026-08-20",
        receipt_sha256="b" * 64,
    )
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(matrix_to_dict([probe])))
    return path


class FakeClient:
    """Scripted responses keyed by task id substring."""

    def __init__(self, script):
        self.script = script  # task_id -> (text, finish_reason) or Exception
        self.calls = []

    def generate(self, request):
        prompt = request.messages[0]["content"]
        task_id = next(t["id"] for t in ALL_TASKS if f"Problem for {t['id']}" in prompt)
        self.calls.append(task_id)
        outcome = self.script.get(task_id, ("Final answer: ?", "stop"))
        if isinstance(outcome, Exception):
            raise outcome
        text, finish = outcome
        return GenerationResult(
            text=text,
            provider="deepseek",
            model="deepseek-v4-flash",
            input_tokens=20,
            output_tokens=10,
            timestamp="2026-07-22T00:00:00+00:00",
            cache_hit=False,
            cache_key=hashlib.sha256(prompt.encode()).hexdigest(),
            request={},
            finish_reason=finish,
            reasoning_tokens=0,
            raw_response={"id": f"resp_{task_id}", "usage": {"prompt_tokens": 20}},
        )


def _paths(tmp_path):
    return {
        "results_dir": tmp_path / "results",
        "budget_path": tmp_path / "budget_state.json",
        "log_path": tmp_path / "cost_log.csv",
        "task_set_manifest_path": tmp_path / "task_set_manifest.json",
        "capability_matrix_path": tmp_path / "capabilities.json",
    }


def _approved_budget(tmp_path):
    p = tmp_path / "budget_state.json"
    approve_historical_spend(0.0, "test", path=p)
    return p


# --------------------------------------------------------------------------- #
# Loading / hash verification / selection
# --------------------------------------------------------------------------- #


def test_load_tasks_schema_validates(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    tasks = load_tasks(tasks_path)
    assert [t.id for t in tasks] == [t["id"] for t in ALL_TASKS]


def test_load_tasks_rejects_malformed(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"id": "math-009"}) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        load_tasks(bad)


def test_verify_task_set_hash_fails_closed_on_mismatch(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    (tmp_path / "task_set_manifest.json").write_text(json.dumps({"task_set_sha256": "0" * 64}))
    with pytest.raises(RuntimeError, match="hash mismatch"):
        verify_task_set_hash(tasks_path, tmp_path / "task_set_manifest.json")


def test_dry_run_pilot_composition():
    tasks = load_tasks_from_records(ALL_TASKS)
    pilot = select_dry_run_pilot(tasks)
    assert [t.id for t in pilot] == ["math-001", "math-002", "logic-001", "factual-001", "ethics-001"]


def load_tasks_from_records(records):
    from src.task_schema import parse_task

    return [parse_task(r) for r in records]


# --------------------------------------------------------------------------- #
# Categorization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"generation_error": "Timeout: x", "finish_reason": None, "final_answer": None, "decision": None}, "generation_failure"),
        ({"generation_error": None, "finish_reason": "length", "final_answer": "42", "decision": "equivalent"}, "truncation"),
        ({"generation_error": None, "finish_reason": "stop", "final_answer": None, "decision": None}, "parse_failure"),
        ({"generation_error": None, "finish_reason": "stop", "final_answer": "NYC", "decision": "unscored"}, "unscored"),
        ({"generation_error": None, "finish_reason": "stop", "final_answer": "41", "decision": "not_equivalent"}, "incorrect"),
        ({"generation_error": None, "finish_reason": "stop", "final_answer": "42", "decision": "equivalent"}, "correct"),
    ],
)
def test_categorize(kwargs, expected):
    assert categorize(**kwargs) == expected
    assert expected in CATEGORIES


# --------------------------------------------------------------------------- #
# End-to-end dry run with a fake client
# --------------------------------------------------------------------------- #


def test_dry_run_end_to_end(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    _write_capabilities(tmp_path)
    _approved_budget(tmp_path)

    script = {
        "math-001": ("Some reasoning.\nFinal answer: 42", "stop"),          # correct
        "math-002": ("Reasoning.\nFinal answer: 8", "stop"),                # incorrect
        "logic-001": ("Reasoning.\nFinal answer: the Alice", "stop"),       # correct (article/case norm)
        "factual-001": ("Reasoning.\nFinal answer: the city of Paris, France", "stop"),  # unscored (long miss)
        "ethics-001": ("Reasoning.\nFinal answer: yes", "stop"),            # agrees with source label
    }
    summary = run_baseline(
        config_path,
        tasks_path,
        FakeClient(script),
        dry_run=True,
        confirmed=True,
        interactive=False,
        code_revision="testrev",
        **_paths(tmp_path),
    )

    assert summary["dry_run"] is True
    assert summary["n_selected"] == 5
    assert summary["n_completed"] == 5
    per = summary["per_task_type"]
    assert per["math"]["categories"]["correct"] == 1
    assert per["math"]["categories"]["incorrect"] == 1
    assert per["math"]["accuracy_excluding_unscored"] == 0.5
    assert per["logic"]["categories"]["correct"] == 1
    assert per["factual"]["categories"]["unscored"] == 1
    # Ethics is reported as source-label agreement, never accuracy.
    assert per["ethics"]["source_label_agreement"] == 1.0
    assert "accuracy_excluding_unscored" not in per["ethics"]

    # Append-only run directory with the full journal and receipts.
    run_dir = tmp_path / "results" / "baseline_test_v1"
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "config.yaml").is_file()
    responses = [json.loads(l) for l in (run_dir / "responses.jsonl").read_text().splitlines()]
    assert len(responses) == 5
    factual = next(r for r in responses if r["task_id"] == "factual-001")
    assert factual["category"] == "unscored"
    assert factual["evaluation"]["decision"] == "unscored"
    journal = (run_dir / "journal.jsonl").read_text()
    for state in ("planned", "submitted", "received", "persisted", "accounted"):
        assert state in journal
    assert (run_dir / "raw" / "math-001__baseline.json").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["experiment_kind"] == "baseline"
    assert manifest["selection_args"]["composition"] == {"math": 2, "logic": 1, "factual": 1, "ethics": 1}
    summary_disk = json.loads((run_dir / "summary.json").read_text())
    assert summary_disk["run_id"] == "baseline_test_v1"

    # Reservation reconciled, none dangling.
    state = load_state(tmp_path / "budget_state.json")
    assert state.reservations == {}


def test_full_run_categories_cover_failures(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    _write_capabilities(tmp_path)
    _approved_budget(tmp_path)

    script = {
        "math-001": RuntimeError("boom"),                        # generation_failure
        "math-002": ("Final answer: 7 but cut off", "length"),   # truncation
        "logic-001": ("no constrained answer line", "stop"),     # parse_failure
        "factual-001": ("Final answer: Paris", "stop"),          # correct
        "ethics-001": ("Final answer: no", "stop"),              # disagrees with source label
    }
    summary = run_baseline(
        config_path, tasks_path, FakeClient(script),
        confirmed=True, interactive=False, code_revision="testrev",
        **_paths(tmp_path),
    )
    per = summary["per_task_type"]
    assert per["math"]["categories"]["generation_failure"] == 1
    assert per["math"]["categories"]["truncation"] == 1
    assert per["logic"]["categories"]["parse_failure"] == 1
    assert per["factual"]["categories"]["correct"] == 1
    assert per["ethics"]["source_label_agreement"] == 0.0
    assert summary["n_completed"] == 5
    # Failure samples recorded for researcher review (C2 step 5).
    failed_ids = {f["task_id"] for f in summary["failure_samples"]}
    assert failed_ids == {"math-001", "math-002", "logic-001"}


def test_resume_is_idempotent(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    _write_capabilities(tmp_path)
    _approved_budget(tmp_path)

    script = {"math-001": ("Final answer: 42", "stop")}
    kwargs = dict(
        dry_run=False, task_ids=["math-001"], confirmed=True, interactive=False,
        code_revision="testrev", **_paths(tmp_path),
    )
    run_baseline(config_path, tasks_path, FakeClient(script), **kwargs)
    # Second identical run resumes: appends nothing new, skips the duplicate.
    summary = run_baseline(config_path, tasks_path, FakeClient(script), **kwargs)
    assert summary["n_completed"] == 0
    assert summary["n_skipped_duplicates"] == 1
    responses = (tmp_path / "results" / "baseline_test_v1" / "responses.jsonl").read_text().splitlines()
    assert len(responses) == 1


def test_capability_gate_fails_closed(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    # No capabilities.json written -> require_valid must stop the run.
    _approved_budget(tmp_path)
    from src.capabilities import CapabilityError

    with pytest.raises((CapabilityError, FileNotFoundError)):
        run_baseline(
            config_path, tasks_path, FakeClient({}),
            confirmed=True, interactive=False, code_revision="testrev",
            **_paths(tmp_path),
        )


def test_expired_probe_blocks_run(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    _approved_budget(tmp_path)
    probe = CapabilityProbe(
        probe_id="p1", provider="deepseek", model="deepseek-v4-flash",
        capability="thinking_disabled", status="pass",
        verified_on="2026-05-01", expires_on="2026-06-01", receipt_sha256="b" * 64,
    )
    (tmp_path / "capabilities.json").write_text(json.dumps(matrix_to_dict([probe])))
    from src.capabilities import CapabilityError

    with pytest.raises(CapabilityError, match="expired"):
        run_baseline(
            config_path, tasks_path, FakeClient({}),
            confirmed=True, interactive=False, code_revision="testrev",
            **_paths(tmp_path),
        )


def test_budget_unapproved_blocks_run(tmp_path):
    tasks_path = _write_task_set(tmp_path)
    config_path = _write_config(tmp_path)
    _write_capabilities(tmp_path)
    from src.budget_state import BudgetNotApprovedError

    with pytest.raises(BudgetNotApprovedError):
        run_baseline(
            config_path, tasks_path, FakeClient({}),
            confirmed=True, interactive=False, code_revision="testrev",
            **_paths(tmp_path),
        )
