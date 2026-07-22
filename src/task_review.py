"""Human review and canonical merge for the Phase 1 task set (Milestone B7).

Review decisions live in a separate immutable JSONL file keyed by task id.
``merge_canonical`` combines candidates with decisions, enforces the B7
acceptance gate, and writes:

- ``data/tasks/task_set.jsonl`` (canonical, sorted by task id)
- ``data/tasks/task_set_manifest.json`` (SHA-256 hashes + generation params)

Acceptance gate (plan.md §B7): exactly 200 approved tasks, 50 per type, 20
Direction-2 probes per type with exactly 10 yes / 10 no labels, no unknown
license, no missing source id, no pending review.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src import schemas
from src.task_schema import TaskRecord, dump_task, load_task_set, parse_task

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = REPO_ROOT / "data" / "tasks"

REVIEW_CHECKS = [
    "prompt_grammatical_self_contained",
    "ground_truth_follows",
    "reasoning_depth_at_least_two_steps",
    "task_type_definition_satisfied",
    "binary_formulation_preserves_reasoning",
    "provenance_license_complete",
    "no_answer_leakage",
]

ACCEPTANCE_PER_TYPE = 50
PROBES_PER_TYPE = 20
PROBE_LABELS_PER_SIDE = 10


class ReviewError(RuntimeError):
    """Review decisions are malformed, duplicated, or fail the acceptance gate."""


@dataclass(frozen=True)
class ReviewDecision:
    task_id: str
    decision: str  # "approved" | "rejected"
    reviewer: str
    reviewed_at: str
    notes: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewDecision":
        try:
            decision = cls(
                task_id=str(data["task_id"]),
                decision=str(data["decision"]),
                reviewer=str(data.get("reviewer", "researcher")),
                reviewed_at=str(data["reviewed_at"]),
                notes=data.get("notes"),
            )
        except KeyError as exc:
            raise ReviewError(f"review decision missing field {exc}: {data!r}") from exc
        if decision.decision not in ("approved", "rejected"):
            raise ReviewError(f"invalid decision {decision.decision!r} for {decision.task_id}")
        return decision


def sha256_file(path: Path) -> str:
    return schemas.sha256_file(path)


# --------------------------------------------------------------------------- #
# Review form export (B7 review table)
# --------------------------------------------------------------------------- #


def export_review_form(candidates_path: Path, form_path: Path) -> int:
    """Write a CSV review form with one row per candidate task."""
    records = load_task_set(candidates_path)
    form_path.parent.mkdir(parents=True, exist_ok=True)
    with form_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "task_id",
                "task_type",
                "direction2_probe",
                "problem",
                "ground_truth",
                "binary_formulation",
                "binary_ground_truth",
                "source_id",
                "source_license",
                *[f"check_{name}" for name in REVIEW_CHECKS],
                "decision",
                "notes",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    record.id,
                    record.task_type,
                    record.direction2_probe,
                    record.problem,
                    record.ground_truth,
                    record.binary_formulation or "",
                    record.binary_ground_truth or "",
                    record.source_id,
                    record.source_license,
                    *[""] * len(REVIEW_CHECKS),
                    "",
                    "",
                ]
            )
    return len(records)


# --------------------------------------------------------------------------- #
# Decision loading (immutable, keyed by task id)
# --------------------------------------------------------------------------- #


def load_decisions(decisions_path: Path) -> Dict[str, ReviewDecision]:
    decisions: Dict[str, ReviewDecision] = {}
    with Path(decisions_path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            decision = ReviewDecision.from_dict(json.loads(line))
            if decision.task_id in decisions:
                raise ReviewError(
                    f"duplicate review decision for {decision.task_id} at line {line_number}; "
                    "decisions are immutable — adjudicate in a new file, not in place"
                )
            decisions[decision.task_id] = decision
    return decisions


# --------------------------------------------------------------------------- #
# Canonical merge + acceptance gate
# --------------------------------------------------------------------------- #


def _gate_failures(records: List[TaskRecord]) -> List[str]:
    failures: List[str] = []
    if len(records) != 4 * ACCEPTANCE_PER_TYPE:
        failures.append(f"expected 200 approved tasks, got {len(records)}")
    by_type: Dict[str, List[TaskRecord]] = {}
    for record in records:
        by_type.setdefault(record.task_type, []).append(record)
    for task_type, group in sorted(by_type.items()):
        if len(group) != ACCEPTANCE_PER_TYPE:
            failures.append(f"{task_type}: expected {ACCEPTANCE_PER_TYPE}, got {len(group)}")
        probes = [r for r in group if r.direction2_probe]
        if len(probes) != PROBES_PER_TYPE:
            failures.append(f"{task_type}: expected {PROBES_PER_TYPE} probes, got {len(probes)}")
        yes = sum(1 for r in probes if r.binary_ground_truth == "yes")
        no = sum(1 for r in probes if r.binary_ground_truth == "no")
        if yes != PROBE_LABELS_PER_SIDE or no != PROBE_LABELS_PER_SIDE:
            failures.append(f"{task_type}: probe labels {yes} yes / {no} no, expected 10/10")
    for record in records:
        if "unknown" in record.source_license.lower():
            failures.append(f"{record.id}: unknown license")
        if not record.source_id:
            failures.append(f"{record.id}: missing source id")
        if record.review_status != "approved":
            failures.append(f"{record.id}: review_status={record.review_status}")
    return failures


def merge_canonical(
    candidates_path: Path,
    decisions_path: Path,
    output_path: Path,
    manifest_path: Path,
    *,
    config_path: Optional[Path] = None,
) -> Path:
    """Merge candidates + review decisions into the canonical task set.

    Every candidate must have a decision; approved tasks get
    ``review_status: approved`` with reviewer notes. The acceptance gate runs
    before anything is written; failures raise ``ReviewError``.
    """
    candidates = load_task_set(candidates_path)
    decisions = load_decisions(decisions_path)

    missing = [record.id for record in candidates if record.id not in decisions]
    if missing:
        raise ReviewError(f"{len(missing)} candidates lack review decisions, e.g. {missing[:3]}")

    approved: List[TaskRecord] = []
    for record in candidates:
        decision = decisions[record.id]
        if decision.decision != "approved":
            continue
        updated = dump_task(record)
        updated["review_status"] = "approved"
        updated["reviewer_notes"] = decision.notes
        approved.append(parse_task(updated))

    failures = _gate_failures(approved)
    if failures:
        raise ReviewError("acceptance gate failed:\n  - " + "\n  - ".join(failures))

    approved.sort(key=lambda record: record.id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in approved:
            handle.write(json.dumps(dump_task(record), ensure_ascii=False) + "\n")

    try:
        task_set_display_path = str(output_path.relative_to(REPO_ROOT))
    except ValueError:
        task_set_display_path = str(output_path)

    manifest: Dict[str, Any] = {
        "schema": "task_set_manifest",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task_set_path": task_set_display_path,
        "task_set_sha256": sha256_file(output_path),
        "candidates_path": str(candidates_path),
        "candidates_sha256": sha256_file(candidates_path),
        "review_decisions_path": str(decisions_path),
        "review_decisions_sha256": sha256_file(decisions_path),
        "task_count": len(approved),
        "selection_config_path": str(config_path) if config_path else None,
        "selection_config_sha256": sha256_file(Path(config_path)) if config_path else None,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path
