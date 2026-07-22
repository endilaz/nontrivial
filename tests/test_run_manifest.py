"""Tests for the A5 append-only, resumable run directory (src/run_manifest.py).

Covers the crash/resume/concurrency behaviors called out in plan.md §A5 and the
E1 engineering-risk register: no-overwrite, exclusive lease, append+fsync,
unique response key (idempotent duplicate vs. reconciliation conflict),
trailing-line recovery, exact-match resume, and pure summary generation.
"""

import json

import pytest

from src.run_manifest import (
    ManifestMismatchError,
    ResponseKeyConflictError,
    RunDirectory,
    RunDirectoryExistsError,
    RunLockError,
    build_manifest,
)


def _manifest(**overrides):
    base = dict(
        run_id="baseline_deepseek_v4_flash_v2",
        experiment_kind="baseline",
        task_set_path="data/tasks/task_set.jsonl",
        task_set_sha256="a" * 64,
        selected_task_ids=["math-001", "logic-001"],
        selection_args={"task_types": ["math", "logic"], "limit": 2, "seed": 7},
        config_sha256="b" * 64,
        prompt_template_version="v1",
        requested_settings={"thinking_setting": "disabled", "temperature": 0.0},
        price_revision="prices-2026-07-21",
        provider_route=None,
        code_revision="deadbee",  # fixed so tests don't depend on git
        created_at="2026-07-21T00:00:00+00:00",
    )
    base.update(overrides)
    return build_manifest(**base)


def _response(task_id="math-001", condition_id="baseline", **extra):
    rec = {
        "task_id": task_id,
        "condition_id": condition_id,
        "mechanism": None,
        "replicate_index": 0,
        "text": "Final answer: 42",
        "cache_key": "c" * 64,
        "timestamp": "2026-07-21T00:00:01+00:00",
        "system_fingerprint": "fp_123",
        "finish_reason": "stop",
        "applied_settings": {"reasoning_effort": "none"},
        "raw_response_path": "raw/math-001.json",
    }
    rec.update(extra)
    return rec


def test_create_writes_manifest_and_refuses_overwrite(tmp_path):
    d = tmp_path / "run"
    rd = RunDirectory.create(d, _manifest())
    assert rd.manifest_path.is_file()
    loaded = rd.load_manifest()
    assert loaded.run_id == "baseline_deepseek_v4_flash_v2"
    assert loaded.cache_schema_version == 2

    # Second create against the same directory must refuse.
    with pytest.raises(RunDirectoryExistsError):
        RunDirectory.create(d, _manifest())


def test_exclusive_lease_blocks_second_holder(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    with rd.lease():
        # A second lease on the same run must fail while the first is held.
        with pytest.raises(RunLockError):
            with RunDirectory.open(tmp_path / "run").lease():
                pass
    # After release, the lease is acquirable again and the lock file is gone.
    assert not rd.lock_path.exists()
    with rd.lease():
        pass


def test_append_is_durable_and_readable(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    assert rd.append_response(_response("math-001")) == "appended"
    assert rd.append_response(_response("logic-001")) == "appended"

    records = list(rd.iter_responses())
    assert [r["task_id"] for r in records] == ["math-001", "logic-001"]
    # Every line is newline-terminated (flushed).
    assert rd.responses_path.read_bytes().endswith(b"\n")


def test_duplicate_identical_key_is_idempotent(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    rd.append_response(_response("math-001"))
    # Re-appending the byte-identical record is accepted without a new line.
    assert rd.append_response(_response("math-001")) == "duplicate_ok"
    assert len(list(rd.iter_responses())) == 1


def test_conflicting_key_raises_for_reconciliation(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    rd.append_response(_response("math-001", text="Final answer: 42"))
    with pytest.raises(ResponseKeyConflictError):
        rd.append_response(_response("math-001", text="Final answer: 99"))


def test_response_key_distinguishes_mechanism_and_replicate(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    # Same task+condition, different mechanism/replicate => distinct keys.
    rd.append_response(_response("math-001", mechanism="instruction_only"))
    rd.append_response(_response("math-001", mechanism="native_prefill"))
    rd.append_response(_response("math-001", mechanism="instruction_only", replicate_index=1))
    assert len(list(rd.iter_responses())) == 3


def test_trailing_partial_line_recovered_earlier_lines_intact(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    rd.append_response(_response("math-001"))
    rd.append_response(_response("logic-001"))
    good = rd.responses_path.read_text(encoding="utf-8")

    # Simulate a crash mid-write: append an unterminated partial JSON line.
    with rd.responses_path.open("a", encoding="utf-8") as fh:
        fh.write('{"task_id": "ethics-001", "condition_id": "baseline"')  # no newline

    fresh = RunDirectory.open(tmp_path / "run")
    assert fresh.recover_trailing_line() is True
    # The two earlier complete lines are byte-for-byte preserved.
    assert fresh.responses_path.read_text(encoding="utf-8") == good
    assert [r["task_id"] for r in fresh.iter_responses()] == ["math-001", "logic-001"]
    # A second recovery is a no-op (file already ends on a newline).
    assert fresh.recover_trailing_line() is False


def test_resume_matches_and_continues(tmp_path):
    d = tmp_path / "run"
    rd = RunDirectory.create(d, _manifest())
    rd.append_response(_response("math-001"))

    resumed = RunDirectory.resume(d, _manifest())  # identical identity
    # Existing key is known → duplicate is idempotent, new key appends.
    assert resumed.append_response(_response("math-001")) == "duplicate_ok"
    assert resumed.append_response(_response("logic-001")) == "appended"
    assert len(list(resumed.iter_responses())) == 2


def test_resume_refuses_on_manifest_mismatch(tmp_path):
    d = tmp_path / "run"
    RunDirectory.create(d, _manifest())
    with pytest.raises(ManifestMismatchError):
        # Different selected task set → not the same run.
        RunDirectory.resume(d, _manifest(selected_task_ids=["math-002"]))


def test_created_at_difference_does_not_block_resume(tmp_path):
    d = tmp_path / "run"
    RunDirectory.create(d, _manifest(created_at="2026-07-21T00:00:00+00:00"))
    # A later invocation with a different created_at still resumes.
    RunDirectory.resume(d, _manifest(created_at="2026-07-22T09:30:00+00:00"))


def test_state_journal_records_transitions(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    rec = _response("math-001")
    key = rd.response_key(rec)
    for state in ("planned", "submitted", "received", "persisted", "accounted"):
        rd.record_transition(key, state)
    assert rd.journal_states(key) == [
        "planned", "submitted", "received", "persisted", "accounted",
    ]


def test_invalid_state_transition_rejected(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    from src.run_manifest import JournalError

    with pytest.raises(JournalError):
        rd.record_transition("some-key", "teleported")


def test_write_summary_is_pure_write(tmp_path):
    rd = RunDirectory.create(tmp_path / "run", _manifest())
    rd.append_response(_response("math-001"))
    out = rd.write_summary({"n_responses": 1, "by_type": {"math": 1}})
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["n_responses"] == 1


def test_manifest_rejects_unknown_experiment_kind(tmp_path):
    with pytest.raises(ValueError):
        _manifest(experiment_kind="not_a_kind")
