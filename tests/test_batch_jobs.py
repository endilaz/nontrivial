"""Tests for the A6 batch lifecycle + idempotent reconciliation (src/batch_jobs.py).

Mirrors the plan's required acceptance scenarios: reordered outputs, one failed
item, an interrupted poller (partial passes), an expired batch, and a repeated
reconciliation pass — reconciliation must be idempotent in every case.
"""

import pytest

from src.batch_jobs import (
    COMPLETED,
    EXPIRED,
    FAILED,
    PENDING,
    RECONCILED,
    SUBMITTED,
    SUCCEEDED,
    BatchItem,
    BatchJobStore,
)


def _store(tmp_path, n=3):
    store = BatchJobStore(tmp_path / "job")
    items = [
        BatchItem(client_request_id=f"crid-{i}", task_key={"task_id": f"t{i}", "condition_id": "c"})
        for i in range(n)
    ]
    store.create("job-1", "openai", "batch", "inputhash", items)
    store.mark_submitted("provider-batch-abc")
    return store


def test_reconcile_matches_by_id_not_order(tmp_path):
    store = _store(tmp_path, 3)
    # Returned OUT OF ORDER relative to submission.
    summary = store.reconcile([
        {"client_request_id": "crid-2", "status": SUCCEEDED, "receipt_ref": "r2"},
        {"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"},
        {"client_request_id": "crid-1", "status": SUCCEEDED, "receipt_ref": "r1"},
    ])
    assert summary["counts"] == {PENDING: 0, SUCCEEDED: 3, FAILED: 0}
    assert summary["state"] == RECONCILED
    job = store.load()
    assert job.items["crid-0"].receipt_ref == "r0"


def test_one_failed_item_is_recorded(tmp_path):
    store = _store(tmp_path, 3)
    summary = store.reconcile([
        {"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"},
        {"client_request_id": "crid-1", "status": FAILED},
        {"client_request_id": "crid-2", "status": SUCCEEDED, "receipt_ref": "r2"},
    ])
    assert summary["counts"] == {PENDING: 0, SUCCEEDED: 2, FAILED: 1}
    assert summary["state"] == COMPLETED  # completed-with-failures, not clean reconciled
    assert summary["resubmit_ids"] == ["crid-1"]  # only the failed item


def test_interrupted_poller_resumes_across_partial_passes(tmp_path):
    store = _store(tmp_path, 3)
    first = store.reconcile([{"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"}])
    assert first["counts"][PENDING] == 2
    assert first["state"] != RECONCILED
    second = store.reconcile([
        {"client_request_id": "crid-1", "status": SUCCEEDED, "receipt_ref": "r1"},
        {"client_request_id": "crid-2", "status": SUCCEEDED, "receipt_ref": "r2"},
    ])
    assert second["counts"] == {PENDING: 0, SUCCEEDED: 3, FAILED: 0}
    assert second["state"] == RECONCILED


def test_repeated_reconciliation_is_idempotent(tmp_path):
    store = _store(tmp_path, 3)
    items = [
        {"client_request_id": f"crid-{i}", "status": SUCCEEDED, "receipt_ref": f"r{i}"}
        for i in range(3)
    ]
    s1 = store.reconcile(items)
    state_after_1 = store.load().to_dict()
    s2 = store.reconcile(items)  # same outputs again
    state_after_2 = store.load().to_dict()

    assert s2["newly_applied"] == []  # nothing new applied the second time
    assert s1["counts"] == s2["counts"]
    # Transitions may not grow on a no-op reconcile of an already-final job.
    assert state_after_1["items"] == state_after_2["items"]


def test_expired_batch_lists_unfinished_for_resubmission(tmp_path):
    store = _store(tmp_path, 3)
    store.reconcile([{"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"}])
    job = store.mark_expired()
    assert job.state == EXPIRED
    # Succeeded item is NOT resubmitted (no duplicate); the rest are.
    assert store.load().resubmit_ids() == ["crid-1", "crid-2"]


def test_succeeded_item_never_downgraded(tmp_path):
    store = _store(tmp_path, 2)
    store.reconcile([{"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"}])
    # A later stray FAILED for an already-succeeded id must not downgrade it.
    store.reconcile([{"client_request_id": "crid-0", "status": FAILED}])
    assert store.load().items["crid-0"].status == SUCCEEDED


def test_unknown_client_request_id_reported_not_applied(tmp_path):
    store = _store(tmp_path, 1)
    summary = store.reconcile([
        {"client_request_id": "crid-0", "status": SUCCEEDED, "receipt_ref": "r0"},
        {"client_request_id": "ghost", "status": SUCCEEDED},
    ])
    assert summary["unknown"] == ["ghost"]
    assert summary["counts"][SUCCEEDED] == 1
