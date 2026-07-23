"""Apply the 2026-07-23 math re-perturbation amendment (contamination fix).

Distinct from ``amend_task_set.py`` (which replaces defective factual tasks
with new ids/sources). This amendment keeps every math task's id, source row,
seed, difficulty bin, probe flag, and binary ground truth; only the perturbed
problem text, transformation log, and derived binary formulation change.

Workflow (B7 gates preserved):

1. ``--prepare``: rebuild math candidates with the expanded dictionaries
   (require_math_transformation=True), splice them into a new
   ``candidates.math_reperturbed.jsonl`` alongside the untouched non-math
   candidates, and write a review form of the 50 changed math rows only.
2. Researcher reviews the form, then runs ``--merge``: the canonical merge +
   acceptance gate runs against the existing review decisions (math rows keep
   their approvals), and the manifest is amended with a
   ``supersedes_task_set_sha256`` chain plus amendment provenance.

Usage:
    .venv/bin/python scripts/amend_math_perturbation.py \
        --amendment configs/task_amendment_math_perturbation_2026-07-23.yaml --prepare
    # researcher reviews data/tasks/review/review_form_math_reperturbation.csv, then:
    .venv/bin/python scripts/amend_math_perturbation.py \
        --amendment configs/task_amendment_math_perturbation_2026-07-23.yaml --merge
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from src import task_set as ts  # noqa: E402
from src.task_review import (  # noqa: E402
    export_review_form,
    merge_canonical,
    sha256_file,
)
from src.task_schema import dump_task, load_task_set  # noqa: E402

CANDIDATES = REPO_ROOT / "data" / "tasks" / "review" / "candidates.jsonl"
DECISIONS = REPO_ROOT / "data" / "tasks" / "review" / "review_decisions.jsonl"
ADJUDICATION = REPO_ROOT / "data" / "tasks" / "review" / "review_decisions.adjudication.jsonl"
TASK_SET = REPO_ROOT / "data" / "tasks" / "task_set.jsonl"
MANIFEST = REPO_ROOT / "data" / "tasks" / "task_set_manifest.json"
SELECTION_CONFIG = REPO_ROOT / "configs" / "task_selection.yaml"

REPERTURBED = REPO_ROOT / "data" / "tasks" / "review" / "candidates.math_reperturbed.jsonl"
REVIEW_FORM = REPO_ROOT / "data" / "tasks" / "review" / "review_form_math_reperturbation.csv"


def load_amendment(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("status") != "approved":
        raise RuntimeError(f"amendment config is not researcher-approved: {path}")
    if data.get("reperturb_task_ids") != "all-math":
        raise RuntimeError(f"unsupported reperturb_task_ids: {data.get('reperturb_task_ids')!r}")
    return data


def cmd_prepare(amendment_path: Path) -> None:
    load_amendment(amendment_path)

    # Rebuild math candidates with the expanded dictionaries, enforcing the
    # >=1-transformation invariant. Non-math records come from the committed
    # candidates file verbatim (proven byte-identical by tests).
    config = ts.load_selection_config(SELECTION_CONFIG)
    rejections = ts.RejectionLog()
    new_math = ts.build_math_tasks(config, rejections, require_transformation=True)
    if len(new_math) != 50:
        raise RuntimeError(f"expected 50 math candidates, got {len(new_math)}")

    old_math_by_id = {
        json.loads(line)["id"]: json.loads(line)
        for line in CANDIDATES.open(encoding="utf-8")
        if line.strip() and json.loads(line)["id"].startswith("math-")
    }
    if set(old_math_by_id) != {r.id for r in new_math}:
        raise RuntimeError("math task id set changed; refusing to re-perturb")

    changed = []
    for record in new_math:
        old = old_math_by_id[record.id]
        new_dump = dump_task(record)
        # Invariants: ids, sources, ground truth, probe flags, binary labels,
        # difficulty bin, and seed all unchanged.
        for key in (
            "id", "source_id", "ground_truth", "direction2_probe",
            "binary_ground_truth", "review_status",
        ):
            if old[key] != new_dump[key]:
                raise RuntimeError(f"{record.id}: {key} changed under re-perturbation")
        if old["math"]["original_problem"] != new_dump["math"]["original_problem"]:
            raise RuntimeError(f"{record.id}: original_problem changed")
        if old["math"]["difficulty_bin"] != new_dump["math"]["difficulty_bin"]:
            raise RuntimeError(f"{record.id}: difficulty_bin changed")
        if not new_dump["math"]["transformations"]:
            raise RuntimeError(f"{record.id}: empty transformations after amendment")
        if json.dumps(old, sort_keys=True) != json.dumps(new_dump, sort_keys=True):
            changed.append(record.id)

    # Every math task must carry >=1 transformation (the amendment invariant);
    # tasks already perturbed in 2026-07-22 may remain byte-identical — the
    # invariant is non-verbatim text, not forced novelty for already-safe rows.
    unperturbed = [
        rid for rid, r in zip(old_math_by_id, new_math, strict=True)
        if not r.math.transformations
    ]
    if unperturbed:
        raise RuntimeError(f"tasks still unperturbed after amendment: {unperturbed}")

    # Splice: non-math candidates verbatim + re-perturbed math.
    spliced = [
        json.loads(line)
        for line in CANDIDATES.open(encoding="utf-8")
        if line.strip() and not json.loads(line)["id"].startswith("math-")
    ] + [dump_task(r) for r in new_math]
    spliced.sort(key=lambda c: c["id"])
    with REPERTURBED.open("w", encoding="utf-8") as fh:
        for record in spliced:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Review form: changed math rows only.
    rep_path = REPERTURBED.with_name("candidates.math_reperturbed.only.jsonl")
    with rep_path.open("w", encoding="utf-8") as fh:
        for record in spliced:
            if record["id"].startswith("math-"):
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    count = export_review_form(rep_path, REVIEW_FORM)
    print(f"re-perturbed math tasks: {len(changed)}")
    print(f"wrote {len(spliced)} spliced candidates -> {REPERTURBED}")
    print(f"wrote review form ({count} rows) -> {REVIEW_FORM}")
    print("next: researcher reviews the form, sets status: approved in the")
    print("amendment config, then re-runs with --merge")


def cmd_merge(amendment_path: Path) -> None:
    amendment = load_amendment(amendment_path)
    if not REPERTURBED.exists():
        raise RuntimeError("run --prepare first to build re-perturbed candidates")
    prior_sha = sha256_file(TASK_SET)

    # Effective decisions = base + adjudication overlay (same as the 2026-07-22
    # factual amendment merge). Math rows keep their existing approvals.
    combined: Dict[str, Any] = {}
    for name in (DECISIONS, ADJUDICATION):
        if not name.exists():
            continue
        for line in name.open(encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                combined[d["task_id"]] = d
    effective_path = DECISIONS.with_name("review_decisions.effective.jsonl")
    with effective_path.open("w", encoding="utf-8") as fh:
        for d in combined.values():
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    merge_canonical(REPERTURBED, effective_path, TASK_SET, MANIFEST, config_path=SELECTION_CONFIG)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat()
    manifest["supersedes_task_set_sha256"] = prior_sha
    manifest["amended_on"] = now
    manifest["amendment_id"] = amendment["amendment_id"]
    manifest["amendment_config_path"] = str(amendment_path)
    manifest["review_decisions_path"] = str(DECISIONS)
    manifest["review_decisions_sha256"] = sha256_file(DECISIONS)
    manifest["amended_task_ids"] = {
        "re_perturbed": sorted(
            json.loads(line)["id"]
            for line in REPERTURBED.open(encoding="utf-8")
            if line.strip() and json.loads(line)["id"].startswith("math-")
        )
    }
    manifest["amendment_reason"] = (
        "contamination control: 41/50 math tasks were verbatim GSM8K; all 50 "
        "re-perturbed in place (same ids/sources/seeds/truths) under expanded "
        "substitution dictionaries with the >=1-transformation invariant"
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"merged re-perturbed canonical set -> {TASK_SET}")
    print(f"new task_set_sha256: {manifest['task_set_sha256']}")
    print(f"supersedes: {prior_sha}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--amendment", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        cmd_prepare(args.amendment)
    else:
        cmd_merge(args.amendment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
