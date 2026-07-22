"""Complete the C2 baseline: judge the unscored answers with the approved judge.

After C3 approval, baseline records whose deterministic evaluation came back
``unscored`` (short-text near-misses — alias candidates) are resolved through
the approved answer-equivalence judge (``configs/judge_answer_equivalence_v1.json``).

Append-only discipline (plan.md global rule 4): the original run directory is
NOT modified. Judged resolutions are written to a sibling file
``judgments_baseline.jsonl`` inside the run directory (new file, never an
overwrite) plus a ``judgment``-schema JSONL under the judge's own run records,
and an updated ``summary.json`` addendum (``summary_judged.json``) that
re-computes per-type categories with judged results. The original
``responses.jsonl`` / ``summary.json`` stay byte-identical for audit.

Offline-testable: the client is injected.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation import evaluate_answer, judge_equivalence, load_judge_config  # noqa: E402
from src.generation import GenerationClient  # noqa: E402

DEFAULT_RUN_DIR = REPO_ROOT / "data" / "results" / "baseline_deepseek_v4_flash_v2_full"
JUDGE_CONFIG = REPO_ROOT / "configs" / "judge_answer_equivalence_v1.json"
JUDGED_OUT = "judgments_baseline.jsonl"
SUMMARY_JUDGED_OUT = "summary_judged.json"


def rescore_unscored(
    run_dir: Path,
    judge_config_path: Path,
    client: GenerationClient,
    *,
    judgments_run_path: Path,
) -> Dict[str, Any]:
    """Judge every unscored baseline record; return the judged summary addendum."""
    judge_config = load_judge_config(judge_config_path)
    responses_path = run_dir / "responses.jsonl"
    records = [json.loads(l) for l in responses_path.open(encoding="utf-8") if l.strip()]

    out_path = run_dir / JUDGED_OUT
    if out_path.exists():
        raise FileExistsError(f"judged resolutions already exist: {out_path} (append-only)")

    resolutions: List[Dict[str, Any]] = []
    for record in records:
        if record["category"] != "unscored":
            continue
        evaluation = judge_equivalence(
            record["final_answer"],
            record["ground_truth"],
            config=judge_config,
            client=client,
            task_id=record["task_id"],
            run_id=f"{record['task_id']}-baseline-judged",
            judgments_path=judgments_run_path,
        )
        resolutions.append(
            {
                "task_id": record["task_id"],
                "condition_id": record["condition_id"],
                "judged_decision": evaluation.decision,  # equivalent|not_equivalent|unscored
                "judge_method": evaluation.method,
                "judge_reason": evaluation.reason,
                "judge_confidence": evaluation.judge.confidence if evaluation.judge else None,
                "resolved_category": (
                    "correct" if evaluation.decision == "equivalent"
                    else "incorrect" if evaluation.decision == "not_equivalent"
                    else "unscored"
                ),
                "judged_on": datetime.now(timezone.utc).isoformat(),
                "judge_id": judge_config.judge_id,
                "rubric_version": judge_config.rubric_version,
            }
        )

    with out_path.open("w", encoding="utf-8") as fh:
        for resolution in resolutions:
            fh.write(json.dumps(resolution, ensure_ascii=False) + "\n")

    # Re-compute per-type categories with resolutions applied.
    by_id = {r["task_id"]: r for r in resolutions}
    per_type: Dict[str, Dict[str, Any]] = {}
    for record in records:
        bucket = per_type.setdefault(
            record["task_type"],
            {"n_tasks": 0, "categories": {c: 0 for c in
             ("generation_failure", "truncation", "parse_failure", "unscored", "incorrect", "correct")}},
        )
        bucket["n_tasks"] += 1
        category = record["category"]
        if category == "unscored" and record["task_id"] in by_id:
            category = by_id[record["task_id"]]["resolved_category"]
        bucket["categories"][category] += 1

    for task_type, bucket in per_type.items():
        decided = bucket["categories"]["correct"] + bucket["categories"]["incorrect"]
        if task_type == "ethics":
            bucket["source_label_agreement"] = (
                bucket["categories"]["correct"] / decided if decided else None
            )
        else:
            bucket["accuracy_excluding_unscored"] = (
                bucket["categories"]["correct"] / decided if decided else None
            )
        bucket["n_unscored"] = bucket["categories"]["unscored"]

    addendum = {
        "schema": "baseline_judged_summary",
        "schema_version": 1,
        "run_id": json.loads((run_dir / "manifest.json").read_text())["run_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "judge_id": judge_config.judge_id,
        "rubric_version": judge_config.rubric_version,
        "n_judged": len(resolutions),
        "n_resolved_correct": sum(1 for r in resolutions if r["resolved_category"] == "correct"),
        "n_resolved_incorrect": sum(1 for r in resolutions if r["resolved_category"] == "incorrect"),
        "n_still_unscored": sum(1 for r in resolutions if r["resolved_category"] == "unscored"),
        "per_task_type": per_type,
        "note": (
            "Addendum: unscored records resolved by the approved judge "
            f"({judge_config.judge_id} rubric {judge_config.rubric_version}). "
            "Original responses.jsonl/summary.json unchanged (append-only)."
        ),
    }
    summary_path = run_dir / SUMMARY_JUDGED_OUT
    summary_path.write_text(json.dumps(addendum, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return addendum


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--judge-config", default=str(JUDGE_CONFIG))
    parser.add_argument("--judgments", default=None,
                        help="judgment-schema JSONL (default: <run-dir>/../../judge_runs/judgments.jsonl)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_dir = Path(args.run_dir)
    judgments = Path(args.judgments) if args.judgments else (
        REPO_ROOT / "data" / "results" / "judge_runs" / "judgments_baseline_full.jsonl"
    )
    client = GenerationClient()
    addendum = rescore_unscored(run_dir, Path(args.judge_config), client, judgments_run_path=judgments)
    print(json.dumps({
        "n_judged": addendum["n_judged"],
        "resolved_correct": addendum["n_resolved_correct"],
        "resolved_incorrect": addendum["n_resolved_incorrect"],
        "still_unscored": addendum["n_still_unscored"],
        "per_task_type": {t: r.get("accuracy_excluding_unscored", r.get("source_label_agreement"))
                          for t, r in addendum["per_task_type"].items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
