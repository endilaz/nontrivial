"""Append-only, resumable run directories (Milestone A5).

Every experiment run (baseline, capability probe, corruption, hint, judge
validation) writes into its own directory whose layout is:

    results/<run_id>/
        manifest.json     # immutable identity of the run (this module)
        config.yaml        # the RunConfig snapshot (optional, caller-supplied)
        responses.jsonl    # append-only, one response per line, fsync'd
        journal.jsonl      # per-request state machine transitions
        summary.json       # derived, PURE (no model calls, no ledger writes)
        raw/               # raw provider receipts, one file per response
        run.lock           # exclusive lease while a process owns the run

Guarantees this module enforces (see plan.md §A5 and the E1/E2 risk register):

- **No overwrite.** ``create`` refuses if a manifest already exists.
- **Exclusive lease.** ``lease()`` uses ``O_CREAT | O_EXCL`` so two processes
  cannot own the same run concurrently.
- **Append + fsync.** Each response line is flushed and ``fsync``'d before the
  caller records its cost as ``accounted`` (crash-consistency).
- **Unique response key** ``(task_id, condition_id, mechanism, replicate_index)``.
  A resume accepts a duplicate only if it is byte-identical; otherwise it raises
  for reconciliation rather than silently picking a winner.
- **Trailing-line recovery only.** An interrupted run may leave one unterminated
  final line; that (and only that) is truncated. Earlier lines are never
  rewritten.
- **Exact-match resume.** ``resume`` refuses unless the on-disk manifest matches
  the requested run on every identity field (``created_at`` excepted).

This module performs NO network or model calls and does not touch the cost
ledger; summary generation here is a pure read→derive→write.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MANIFEST_NAME = "manifest.json"
CONFIG_NAME = "config.yaml"
RESPONSES_NAME = "responses.jsonl"
JOURNAL_NAME = "journal.jsonl"
SUMMARY_NAME = "summary.json"
LOCK_NAME = "run.lock"
RAW_DIRNAME = "raw"

MANIFEST_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 2

#: The per-request state machine (plan.md §A5).
STATES: Tuple[str, ...] = ("planned", "submitted", "received", "persisted", "accounted")

EXPERIMENT_KINDS = (
    "baseline",
    "capability_probe",
    "corruption",
    "hint",
    "judge_validation",
)

#: Identity fields compared on resume. ``created_at`` is per-invocation and
#: excluded; everything else (including ``code_revision``) must match, so a code
#: change forces a fresh run / reconciliation rather than a silent resume.
_MATCH_FIELDS = (
    "manifest_schema_version",
    "run_id",
    "experiment_kind",
    "code_revision",
    "task_set_path",
    "task_set_sha256",
    "selected_task_ids",
    "selection_args",
    "config_sha256",
    "prompt_template_version",
    "provider_route",
    "requested_settings",
    "price_revision",
    "cache_schema_version",
)

#: Fields that form the unique response key.
RESPONSE_KEY_FIELDS = ("task_id", "condition_id", "mechanism", "replicate_index")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class RunDirectoryExistsError(RuntimeError):
    """A run directory already has a manifest and must not be overwritten."""


class RunLockError(RuntimeError):
    """Another process holds the exclusive run lease."""


class ManifestMismatchError(RuntimeError):
    """The on-disk manifest does not match the requested run (no safe resume)."""


class ResponseKeyConflictError(RuntimeError):
    """A response with this key already exists and differs byte-for-byte."""


class JournalError(RuntimeError):
    """A malformed non-trailing line was found, or an invalid state transition."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: Any) -> str:
    """Deterministic JSON (sorted keys, compact) for hashing / equality."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def current_code_revision(root: Union[str, Path]) -> str:
    """Return ``"<sha>"`` or ``"<sha>-dirty"`` (``"unknown"`` outside a repo).

    The dirty suffix records that the worktree had uncommitted changes when the
    run was created (plan.md §A5: "git commit plus dirty-worktree hash/status").
    """
    root = str(root)
    try:
        sha = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"
    try:
        dirty = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        dirty = ""
    return f"{sha}-dirty" if dirty else sha


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


class RunManifest(BaseModel):
    """Immutable identity of one run (plan.md §A5 required fields)."""

    run_id: str
    experiment_kind: str
    created_at: str
    code_revision: str
    task_set_path: str  # relative path
    task_set_sha256: str
    selected_task_ids: List[str]
    selection_args: Dict[str, Any]
    config_sha256: str
    prompt_template_version: str
    provider_route: Optional[str] = None
    requested_settings: Dict[str, Any]
    price_revision: str
    cache_schema_version: int = CACHE_SCHEMA_VERSION
    manifest_schema_version: int = MANIFEST_SCHEMA_VERSION

    model_config = {"extra": "forbid"}

    def matches(self, other: "RunManifest") -> bool:
        """True iff every identity field (``created_at`` excepted) is equal."""
        return all(
            getattr(self, f) == getattr(other, f) for f in _MATCH_FIELDS
        )

    def mismatched_fields(self, other: "RunManifest") -> List[str]:
        return [f for f in _MATCH_FIELDS if getattr(self, f) != getattr(other, f)]


def build_manifest(
    *,
    run_id: str,
    experiment_kind: str,
    task_set_path: str,
    task_set_sha256: str,
    selected_task_ids: List[str],
    selection_args: Dict[str, Any],
    config_sha256: str,
    prompt_template_version: str,
    requested_settings: Dict[str, Any],
    price_revision: str,
    provider_route: Optional[str] = None,
    code_revision: Optional[str] = None,
    created_at: Optional[str] = None,
    root: Union[str, Path] = ".",
) -> RunManifest:
    """Construct a :class:`RunManifest`, filling ``code_revision``/``created_at``."""
    if experiment_kind not in EXPERIMENT_KINDS:
        raise ValueError(
            f"experiment_kind={experiment_kind!r} not in {EXPERIMENT_KINDS}"
        )
    return RunManifest(
        run_id=run_id,
        experiment_kind=experiment_kind,
        created_at=created_at or _now_iso(),
        code_revision=code_revision or current_code_revision(root),
        task_set_path=task_set_path,
        task_set_sha256=task_set_sha256,
        selected_task_ids=list(selected_task_ids),
        selection_args=selection_args,
        config_sha256=config_sha256,
        prompt_template_version=prompt_template_version,
        provider_route=provider_route,
        requested_settings=requested_settings,
        price_revision=price_revision,
    )


# --------------------------------------------------------------------------- #
# Run directory
# --------------------------------------------------------------------------- #


class RunDirectory:
    """Owns one ``results/<run_id>/`` directory and its append-only files."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self._keys: Optional[Dict[str, str]] = None  # key_str -> canonical(record)

    # -- paths ------------------------------------------------------------- #

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_NAME

    @property
    def responses_path(self) -> Path:
        return self.path / RESPONSES_NAME

    @property
    def journal_path(self) -> Path:
        return self.path / JOURNAL_NAME

    @property
    def summary_path(self) -> Path:
        return self.path / SUMMARY_NAME

    @property
    def lock_path(self) -> Path:
        return self.path / LOCK_NAME

    @property
    def raw_dir(self) -> Path:
        return self.path / RAW_DIRNAME

    # -- lifecycle --------------------------------------------------------- #

    @classmethod
    def create(
        cls,
        path: Union[str, Path],
        manifest: RunManifest,
        config_text: Optional[str] = None,
    ) -> "RunDirectory":
        """Create a fresh run directory. Refuses to overwrite an existing run."""
        rd = cls(path)
        if rd.manifest_path.exists():
            raise RunDirectoryExistsError(
                f"run directory already has a manifest: {rd.manifest_path}"
            )
        rd.path.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            rd.manifest_path,
            json.dumps(manifest.model_dump(), indent=2) + "\n",
        )
        if config_text is not None:
            _atomic_write_text(rd.path / CONFIG_NAME, config_text)
        return rd

    @classmethod
    def open(cls, path: Union[str, Path]) -> "RunDirectory":
        """Open an existing run directory (must contain a manifest)."""
        rd = cls(path)
        if not rd.manifest_path.exists():
            raise FileNotFoundError(f"no manifest at {rd.manifest_path}")
        return rd

    @classmethod
    def resume(
        cls, path: Union[str, Path], requested: RunManifest
    ) -> "RunDirectory":
        """Open an existing run for resumption; refuse unless the manifest matches.

        Recovers a single unterminated trailing response line if present.
        """
        rd = cls.open(path)
        existing = rd.load_manifest()
        if not existing.matches(requested):
            raise ManifestMismatchError(
                "cannot resume: manifest differs on "
                + ", ".join(existing.mismatched_fields(requested))
            )
        rd.recover_trailing_line()
        return rd

    def load_manifest(self) -> RunManifest:
        return RunManifest.model_validate_json(self.manifest_path.read_text("utf-8"))

    # -- exclusive lease --------------------------------------------------- #

    def lease(self) -> "_RunLease":
        return _RunLease(self)

    # -- responses --------------------------------------------------------- #

    def response_key(self, record: Dict[str, Any]) -> str:
        """Canonical string form of the unique response key for ``record``."""
        for required in ("task_id", "condition_id"):
            if required not in record:
                raise ValueError(f"response record missing {required!r} for its key")
        key = {
            "task_id": record["task_id"],
            "condition_id": record["condition_id"],
            "mechanism": record.get("mechanism"),
            "replicate_index": record.get("replicate_index", 0),
        }
        return canonical_json(key)

    def _load_keys(self) -> Dict[str, str]:
        if self._keys is None:
            keys: Dict[str, str] = {}
            for record in self.iter_responses():
                keys[self.response_key(record)] = canonical_json(record)
            self._keys = keys
        return self._keys

    def append_response(self, record: Dict[str, Any]) -> str:
        """Append one response line (flush + fsync).

        Returns ``"appended"`` for a new key, or ``"duplicate_ok"`` when an
        identical record already exists (idempotent resume). Raises
        :class:`ResponseKeyConflictError` when the key exists but the content
        differs.
        """
        keys = self._load_keys()
        key_str = self.response_key(record)
        canon = canonical_json(record)
        if key_str in keys:
            if keys[key_str] == canon:
                return "duplicate_ok"
            raise ResponseKeyConflictError(
                f"response key already present with different content: {key_str}"
            )
        _append_line_fsync(self.responses_path, json.dumps(record, ensure_ascii=False))
        keys[key_str] = canon
        return "appended"

    def iter_responses(self) -> Iterator[Dict[str, Any]]:
        """Yield each complete response record. Raises on a malformed line."""
        if not self.responses_path.exists():
            return
        with self.responses_path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.endswith("\n"):
                    # Unterminated trailing line: an interrupted write. Skip it
                    # here; recover_trailing_line() truncates it on resume.
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    yield json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise JournalError(
                        f"malformed response line {lineno} in {self.responses_path}"
                    ) from exc

    def recover_trailing_line(self) -> bool:
        """Truncate a single unterminated trailing line, if present.

        Never rewrites earlier lines. Returns True if a partial line was removed.
        """
        p = self.responses_path
        if not p.exists():
            return False
        data = p.read_bytes()
        if not data:
            return False
        nl = data.rfind(b"\n")
        tail = data[nl + 1:]  # bytes after the last newline (b"" if ends with \n)
        if not tail:
            return False
        complete_len = nl + 1 if nl != -1 else 0
        with p.open("r+b") as fh:
            fh.truncate(complete_len)
            fh.flush()
            os.fsync(fh.fileno())
        self._keys = None  # force rebuild of the key index
        return True

    # -- state journal ----------------------------------------------------- #

    def record_transition(self, key_str: str, state: str) -> None:
        """Append a state transition for a response key (flush + fsync)."""
        if state not in STATES:
            raise JournalError(f"unknown state {state!r}; expected one of {STATES}")
        entry = {"key": key_str, "state": state, "ts": _now_iso()}
        _append_line_fsync(self.journal_path, json.dumps(entry, ensure_ascii=False))

    def journal_states(self, key_str: str) -> List[str]:
        """Ordered list of states recorded for ``key_str``."""
        states: List[str] = []
        if not self.journal_path.exists():
            return states
        with self.journal_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.endswith("\n"):
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                entry = json.loads(stripped)
                if entry.get("key") == key_str:
                    states.append(entry["state"])
        return states

    # -- summary (pure) ---------------------------------------------------- #

    def write_summary(self, summary: Dict[str, Any]) -> Path:
        """Atomically write ``summary.json``.

        Pure by construction: this takes a plain dict and writes it. It never
        calls a model or mutates the cost ledger (plan.md §A5).
        """
        _atomic_write_text(
            self.summary_path, json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
        )
        return self.summary_path


class _RunLease:
    """Context manager acquiring an exclusive ``run.lock`` via ``O_EXCL``."""

    def __init__(self, run_dir: RunDirectory) -> None:
        self._run_dir = run_dir
        self._acquired = False

    def __enter__(self) -> "_RunLease":
        path = self._run_dir.lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RunLockError(
                f"run is locked by another process: {path}"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {"pid": os.getpid(), "host": socket.gethostname(), "acquired_at": _now_iso()},
                fh,
            )
            fh.flush()
            os.fsync(fh.fileno())
        self._acquired = True
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._acquired:
            try:
                self._run_dir.lock_path.unlink()
            except FileNotFoundError:  # pragma: no cover - already released
                pass
            self._acquired = False


# --------------------------------------------------------------------------- #
# Low-level durable writes
# --------------------------------------------------------------------------- #


def _append_line_fsync(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
