"""Preserve-and-invalidate the 2026-07-21 audited artifacts (Milestone A2).

The 2026-07-21 audit (see ``plan.md`` §0.2 and the ``CLAUDE.md`` decisions log)
ruled the generated task set, the baseline/smoke result files, the v1 cache
receipts, and the cost ledger scientifically invalid. They must be **preserved
for audit** — never deleted, never silently repaired in place — and moved out of
the canonical locations so no default loader consumes them.

This module moves each invalid artifact into a dated ``invalidated/2026-07-21/``
sibling directory and records a SHA-256 manifest (original path, new path, hash,
size, run id, reason). It is **idempotent**: re-running verifies the already-moved
state and rewrites an identical manifest. It never touches downloaded source
datasets (now under ``data/raw/``, e.g. ``MATH_gsm8k/``, ``REASONING_hotpot-master/``).

Safety properties:

- All conflicts (an original and an already-invalidated copy that differ) are
  detected up front; ``execute`` raises before moving anything, so a run either
  fully applies its non-conflicting moves or aborts without partial mutation.
- A move is copy-then-verify-then-remove: the destination hash is confirmed equal
  to the source hash before the original is unlinked.
- A both-present-but-byte-identical pair is treated as a redundant leftover; the
  original is removed and the destination kept (still idempotent).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The audit cohort. Fixed by the 2026-07-21 audit — not "today's" date.
INVALIDATION_DATE = "2026-07-21"

MANIFEST_SCHEMA = "invalidation_manifest"
MANIFEST_SCHEMA_VERSION = 1

#: Committed, machine-readable audit record. Note: it may reference gitignored
#: ``data/cache/`` blobs by hash; that is intentional (the manifest is the audit
#: trail even though regenerable cache blobs are not themselves committed).
DEFAULT_MANIFEST_REL = f"data/invalidation/manifest_{INVALIDATION_DATE}.json"

_REASON_SUMMARY = (
    "Invalidated by the 2026-07-21 project audit: invalid math answer labels, "
    "invalid ETHICS label semantics, missing HotpotQA context/provenance, invalid "
    "binary formulations, unenforced thinking settings, untracked LLM-judge calls, "
    "and duplicate cache-hit cost accounting. Preserved for audit; regenerated "
    "canonically under new schema versions and run IDs."
)

_CHUNK = 1 << 20  # 1 MiB


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class InvalidationConflictError(RuntimeError):
    """An original artifact and its invalidated copy exist but differ.

    Raised before any move so the operator can reconcile rather than have the
    tool silently pick a winner.
    """


# --------------------------------------------------------------------------- #
# Rules — what gets invalidated, and why
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InvalidationRule:
    category: str
    reason_code: str
    reason: str
    source_dir: str  # relative to root; scanned NON-recursively
    dest_dir: str  # relative to root
    selector: Callable[[str], bool]  # applied to a file's basename


def _default_rules() -> List[InvalidationRule]:
    dated = f"invalidated/{INVALIDATION_DATE}"
    return [
        InvalidationRule(
            category="task_set",
            reason_code="INVALID_TASK_SET",
            reason=(
                "Generated task set with invalid math labels after numeric "
                "perturbation, invalid ETHICS label semantics, missing HotpotQA "
                "context/provenance, and invalid/missing binary formulations. "
                "Rebuilt under Milestone B."
            ),
            source_dir="data/tasks",
            dest_dir=f"data/tasks/{dated}",
            selector=lambda name: name == "task_set.jsonl",
        ),
        InvalidationRule(
            category="baseline_result",
            reason_code="INVALID_BASELINE_RESULT",
            reason=(
                "Baseline generation/accuracy output built on the invalidated task "
                "set, with unenforced thinking settings and untracked judge calls. "
                "Re-run under Milestone C after valid tasks and evaluation exist."
            ),
            source_dir="data/results",
            dest_dir=f"data/results/{dated}",
            selector=lambda name: name.startswith("baseline_"),
        ),
        InvalidationRule(
            category="smoke",
            reason_code="INVALID_SMOKE",
            reason=(
                "Smoke/capability run did not exercise prefill and recorded "
                "disabled-thinking despite nonzero reasoning tokens in the raw "
                "receipt. Re-run only after Milestone A3."
            ),
            source_dir="data/results",
            dest_dir=f"data/results/{dated}",
            selector=lambda name: name.startswith("smoke_test"),
        ),
        InvalidationRule(
            category="cache_v1",
            reason_code="INVALID_CACHE_V1",
            reason=(
                "Cache schema v1 receipts: the recorded thinking_setting cannot be "
                "trusted as actually applied. Superseded by cache schema v2 under "
                "data/cache/v2/."
            ),
            source_dir="data/cache",
            dest_dir=f"data/cache/{dated}",
            selector=lambda name: name.endswith(".json"),
        ),
        InvalidationRule(
            category="cost_ledger",
            reason_code="INVALID_COST_LEDGER",
            reason=(
                "Cost ledger inflated by duplicate cache-hit accounting and missing "
                "prior judge spend. Not a trustworthy remaining-budget source until "
                "A4 reconciliation against provider dashboards."
            ),
            source_dir="logs",
            dest_dir=f"logs/{dated}",
            selector=lambda name: name == "cost_log.csv",
        ),
    ]


# --------------------------------------------------------------------------- #
# Planned actions
# --------------------------------------------------------------------------- #

# Action values
MOVE = "move"
ALREADY = "already_invalidated"
CONFLICT = "conflict"


@dataclass
class PlannedAction:
    category: str
    reason_code: str
    reason: str
    original_path: str  # relative to root
    invalidated_path: str  # relative to root
    action: str  # MOVE | ALREADY | CONFLICT
    sha256: Optional[str]  # final (destination) hash for MOVE/ALREADY; None for CONFLICT
    size_bytes: Optional[int]
    run_id: Optional[str]
    duplicate_original: bool = False  # both exist & identical → remove the leftover original


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_id_for(category: str, name: str) -> Optional[str]:
    if category in ("baseline_result", "smoke"):
        return name.split(".", 1)[0]
    return None


def _git_revision(root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def _list_files(directory: Path, selector: Callable[[str], bool]) -> List[str]:
    if not directory.is_dir():
        return []
    return sorted(
        p.name
        for p in directory.iterdir()
        if p.is_file() and selector(p.name)
    )


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def build_plan(
    root: Path | str = REPO_ROOT,
    rules: Optional[List[InvalidationRule]] = None,
) -> List[PlannedAction]:
    """Compute the (pure, side-effect-free) invalidation plan for ``root``.

    Considers both the canonical source location and the dated invalidated
    directory, so an already-applied invalidation is reported as ``ALREADY``
    rather than as new work.
    """
    root = Path(root)
    rules = rules if rules is not None else _default_rules()
    actions: List[PlannedAction] = []

    for rule in rules:
        src_dir = root / rule.source_dir
        dst_dir = root / rule.dest_dir

        names = set(_list_files(src_dir, rule.selector))
        names |= set(_list_files(dst_dir, rule.selector))

        for name in sorted(names):
            src = src_dir / name
            dst = dst_dir / name
            original_rel = str((Path(rule.source_dir) / name))
            invalid_rel = str((Path(rule.dest_dir) / name))
            run_id = _run_id_for(rule.category, name)

            src_exists = src.is_file()
            dst_exists = dst.is_file()

            if src_exists and not dst_exists:
                actions.append(
                    PlannedAction(
                        category=rule.category,
                        reason_code=rule.reason_code,
                        reason=rule.reason,
                        original_path=original_rel,
                        invalidated_path=invalid_rel,
                        action=MOVE,
                        sha256=sha256_file(src),
                        size_bytes=src.stat().st_size,
                        run_id=run_id,
                    )
                )
            elif dst_exists and not src_exists:
                actions.append(
                    PlannedAction(
                        category=rule.category,
                        reason_code=rule.reason_code,
                        reason=rule.reason,
                        original_path=original_rel,
                        invalidated_path=invalid_rel,
                        action=ALREADY,
                        sha256=sha256_file(dst),
                        size_bytes=dst.stat().st_size,
                        run_id=run_id,
                    )
                )
            elif src_exists and dst_exists:
                src_hash = sha256_file(src)
                dst_hash = sha256_file(dst)
                if src_hash == dst_hash:
                    actions.append(
                        PlannedAction(
                            category=rule.category,
                            reason_code=rule.reason_code,
                            reason=rule.reason,
                            original_path=original_rel,
                            invalidated_path=invalid_rel,
                            action=ALREADY,
                            sha256=dst_hash,
                            size_bytes=dst.stat().st_size,
                            run_id=run_id,
                            duplicate_original=True,
                        )
                    )
                else:
                    actions.append(
                        PlannedAction(
                            category=rule.category,
                            reason_code=rule.reason_code,
                            reason=rule.reason,
                            original_path=original_rel,
                            invalidated_path=invalid_rel,
                            action=CONFLICT,
                            sha256=None,
                            size_bytes=None,
                            run_id=run_id,
                        )
                    )

    actions.sort(key=lambda a: (a.category, a.original_path))
    return actions


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


@dataclass
class InvalidationResult:
    dry_run: bool
    actions: List[PlannedAction]
    manifest: Dict[str, object]
    manifest_path: Optional[Path]

    @property
    def moved(self) -> List[PlannedAction]:
        return [a for a in self.actions if a.action == MOVE]

    @property
    def already(self) -> List[PlannedAction]:
        return [a for a in self.actions if a.action == ALREADY]

    @property
    def conflicts(self) -> List[PlannedAction]:
        return [a for a in self.actions if a.action == CONFLICT]


def _move_verified(src: Path, dst: Path) -> None:
    """Copy src → dst, verify the hash matches, then remove src."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    before = sha256_file(src)
    shutil.copy2(src, dst)
    after = sha256_file(dst)
    if before != after:
        # Extremely unlikely (disk corruption). Leave both in place, remove the
        # partial destination, and fail loudly.
        dst.unlink(missing_ok=True)
        raise RuntimeError(
            f"hash mismatch copying {src} -> {dst}: {before} != {after}"
        )
    src.unlink()


def _build_manifest(root: Path, actions: List[PlannedAction]) -> Dict[str, object]:
    artifacts = [
        {
            "category": a.category,
            "reason_code": a.reason_code,
            "reason": a.reason,
            "run_id": a.run_id,
            "original_path": a.original_path,
            "invalidated_path": a.invalidated_path,
            "sha256": a.sha256,
            "size_bytes": a.size_bytes,
        }
        for a in actions
        if a.action in (MOVE, ALREADY)
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "invalidation_date": INVALIDATION_DATE,
        "generated_by": "src/invalidation.py",
        "code_revision": _git_revision(root),
        "reason_summary": _REASON_SUMMARY,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def _write_manifest_atomic(path: Path, manifest: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def execute(
    root: Path | str = REPO_ROOT,
    *,
    dry_run: bool = False,
    manifest_rel: str = DEFAULT_MANIFEST_REL,
    rules: Optional[List[InvalidationRule]] = None,
) -> InvalidationResult:
    """Apply (or, with ``dry_run``, only plan) the 2026-07-21 invalidation.

    Idempotent. Raises :class:`InvalidationConflictError` — before performing any
    move — if any original artifact conflicts with an existing invalidated copy.
    """
    root = Path(root)
    actions = build_plan(root, rules=rules)

    conflicts = [a for a in actions if a.action == CONFLICT]
    if conflicts:
        listing = ", ".join(a.original_path for a in conflicts)
        raise InvalidationConflictError(
            "original and invalidated copies differ; reconcile manually: " + listing
        )

    manifest = _build_manifest(root, actions)
    manifest_path = root / manifest_rel

    if dry_run:
        return InvalidationResult(
            dry_run=True, actions=actions, manifest=manifest, manifest_path=None
        )

    for a in actions:
        src = root / a.original_path
        dst = root / a.invalidated_path
        if a.action == MOVE:
            _move_verified(src, dst)
        elif a.action == ALREADY and a.duplicate_original and src.is_file():
            # Redundant leftover original that is byte-identical to the preserved
            # copy: drop it so the state is clean and re-runs stay a no-op.
            src.unlink()

    _write_manifest_atomic(manifest_path, manifest)

    return InvalidationResult(
        dry_run=False,
        actions=actions,
        manifest=manifest,
        manifest_path=manifest_path,
    )
