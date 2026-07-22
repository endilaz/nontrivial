"""Batch-job lifecycle + idempotent reconciliation (Milestone A6; risk E5).

Provider Batch APIs are a SEPARATE execution lane, not a flag on the synchronous
runner: results come back out of order, possibly partial, and a job can expire or
be cancelled. This module models the lifecycle explicitly and makes
reconciliation **order-independent and idempotent**:

- Each item carries a stable ``client_request_id``; returned items are matched by
  that id, never by output order.
- Reconciling the same output twice, or in several partial passes, converges to
  the same state (an interrupted poller can safely resume).
- Resubmission asks only for items not already succeeded, so a retry never
  duplicates a receipt already collected.

Persistence is a single JSON state file per job (atomic writes) guarded by an
exclusive lease. No network calls live here — the caller submits/polls the
provider and feeds returned items into :meth:`reconcile`.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

BATCH_SCHEMA = "batch_job"
BATCH_SCHEMA_VERSION = 1

# Lifecycle states.
PLANNED = "planned"
SUBMITTED = "submitted"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
EXPIRED = "expired"
CANCELLED = "cancelled"
RECONCILED = "reconciled"
_STATES = (PLANNED, SUBMITTED, IN_PROGRESS, COMPLETED, EXPIRED, CANCELLED, RECONCILED)

# Per-item statuses.
PENDING = "pending"
SUCCEEDED = "succeeded"
FAILED = "failed"


class BatchJobError(RuntimeError):
    pass


class BatchLockError(RuntimeError):
    """Another process holds the batch-job lease."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@dataclass
class BatchItem:
    client_request_id: str
    task_key: Dict[str, Any]
    status: str = PENDING
    receipt_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_request_id": self.client_request_id,
            "task_key": self.task_key,
            "status": self.status,
            "receipt_ref": self.receipt_ref,
        }


@dataclass
class BatchJob:
    job_id: str
    provider: str
    lane: str  # e.g. "batch"
    input_file_sha256: str
    state: str = PLANNED
    provider_batch_id: Optional[str] = None
    submitted_at: Optional[str] = None
    output_file_sha256: Optional[str] = None
    error_file_sha256: Optional[str] = None
    items: Dict[str, BatchItem] = field(default_factory=dict)
    transitions: List[Dict[str, str]] = field(default_factory=list)

    # -- derived -----------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        c = {PENDING: 0, SUCCEEDED: 0, FAILED: 0}
        for it in self.items.values():
            c[it.status] = c.get(it.status, 0) + 1
        return c

    def resubmit_ids(self) -> List[str]:
        """Client-request ids not yet succeeded (safe to resubmit; no dup)."""
        return sorted(
            crid for crid, it in self.items.items() if it.status != SUCCEEDED
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": BATCH_SCHEMA,
            "schema_version": BATCH_SCHEMA_VERSION,
            "job_id": self.job_id,
            "provider": self.provider,
            "lane": self.lane,
            "input_file_sha256": self.input_file_sha256,
            "state": self.state,
            "provider_batch_id": self.provider_batch_id,
            "submitted_at": self.submitted_at,
            "output_file_sha256": self.output_file_sha256,
            "error_file_sha256": self.error_file_sha256,
            "items": {crid: it.to_dict() for crid, it in self.items.items()},
            "transitions": self.transitions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchJob":
        if data.get("schema_version") != BATCH_SCHEMA_VERSION:
            raise ValueError(
                f"batch_job schema_version {data.get('schema_version')} unsupported"
            )
        job = cls(
            job_id=data["job_id"],
            provider=data["provider"],
            lane=data["lane"],
            input_file_sha256=data["input_file_sha256"],
            state=data["state"],
            provider_batch_id=data.get("provider_batch_id"),
            submitted_at=data.get("submitted_at"),
            output_file_sha256=data.get("output_file_sha256"),
            error_file_sha256=data.get("error_file_sha256"),
            transitions=list(data.get("transitions", [])),
        )
        for crid, it in data.get("items", {}).items():
            job.items[crid] = BatchItem(
                client_request_id=it["client_request_id"],
                task_key=it["task_key"],
                status=it.get("status", PENDING),
                receipt_ref=it.get("receipt_ref"),
            )
        return job

    def _transition(self, state: str) -> None:
        if state not in _STATES:
            raise BatchJobError(f"unknown batch state {state!r}")
        self.state = state
        self.transitions.append({"state": state, "ts": _now_iso()})


class _JobLease:
    def __init__(self, state_path: Path) -> None:
        self._lock = state_path.with_suffix(state_path.suffix + ".lock")

    def __enter__(self) -> "_JobLease":
        self._lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise BatchLockError(f"batch job locked: {self._lock}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname()}, fh)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self._lock.unlink()
        except FileNotFoundError:  # pragma: no cover
            pass


class BatchJobStore:
    """Persists one :class:`BatchJob` to ``<dir>/batch_job.json`` (atomic)."""

    STATE_NAME = "batch_job.json"

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    @property
    def state_path(self) -> Path:
        return self.path / self.STATE_NAME

    def lease(self) -> _JobLease:
        return _JobLease(self.state_path)

    def create(
        self,
        job_id: str,
        provider: str,
        lane: str,
        input_file_sha256: str,
        items: Iterable[BatchItem],
    ) -> BatchJob:
        if self.state_path.exists():
            raise BatchJobError(f"batch job already exists at {self.state_path}")
        job = BatchJob(
            job_id=job_id, provider=provider, lane=lane,
            input_file_sha256=input_file_sha256,
        )
        for it in items:
            if it.client_request_id in job.items:
                raise BatchJobError(f"duplicate client_request_id {it.client_request_id!r}")
            job.items[it.client_request_id] = it
        job._transition(PLANNED)
        self._save(job)
        return job

    def load(self) -> BatchJob:
        return BatchJob.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def _save(self, job: BatchJob) -> None:
        _atomic_write_json(self.state_path, job.to_dict())

    # -- lifecycle transitions (each takes the lease) ---------------------- #

    def mark_submitted(self, provider_batch_id: str) -> BatchJob:
        with self.lease():
            job = self.load()
            job.provider_batch_id = provider_batch_id
            job.submitted_at = _now_iso()
            job._transition(SUBMITTED)
            self._save(job)
            return job

    def mark_expired(self) -> BatchJob:
        return self._mark_terminal(EXPIRED)

    def mark_cancelled(self) -> BatchJob:
        return self._mark_terminal(CANCELLED)

    def _mark_terminal(self, state: str) -> BatchJob:
        with self.lease():
            job = self.load()
            job._transition(state)
            self._save(job)
            return job

    def reconcile(self, returned_items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply returned items (matched by ``client_request_id``), idempotently.

        Each returned item is ``{"client_request_id", "status" (succeeded|failed),
        "receipt_ref"?}``. Unknown ids are reported as ``unknown`` and ignored.
        Order does not matter; re-applying the same items is a no-op. Returns a
        summary with per-status counts and any unknown/newly-applied ids.
        """
        with self.lease():
            job = self.load()
            newly_applied: List[str] = []
            unknown: List[str] = []
            for ret in returned_items:
                crid = ret.get("client_request_id")
                status = ret.get("status")
                if crid not in job.items:
                    unknown.append(crid)
                    continue
                if status not in (SUCCEEDED, FAILED):
                    raise BatchJobError(f"invalid returned status {status!r} for {crid!r}")
                item = job.items[crid]
                # Idempotent: applying an already-succeeded item again is a no-op.
                if item.status == status and item.receipt_ref == ret.get("receipt_ref"):
                    continue
                # Never downgrade a succeeded item back to failed/pending.
                if item.status == SUCCEEDED and status != SUCCEEDED:
                    continue
                item.status = status
                item.receipt_ref = ret.get("receipt_ref")
                newly_applied.append(crid)
            counts = job.counts()
            if counts[PENDING] == 0 and job.state not in (EXPIRED, CANCELLED):
                job._transition(RECONCILED if counts[FAILED] == 0 else COMPLETED)
            elif job.state == SUBMITTED:
                job._transition(IN_PROGRESS)
            self._save(job)
            return {
                "counts": counts,
                "newly_applied": sorted(newly_applied),
                "unknown": sorted(x for x in unknown if x is not None),
                "state": job.state,
                "resubmit_ids": job.resubmit_ids(),
            }
