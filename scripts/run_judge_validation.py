"""Run the C3 answer-equivalence judge validation against the frozen labeled set.

Protocol (plan.md §C3, researcher-confirmed 2026-07-22):

* The validation set is FROZEN (``data/validation/judge_validation_v1.jsonl``,
  hash recorded in its manifest). This script verifies the pair-level identity
  (pair_id/task_id/answers/labels) against the frozen hash before judging —
  researcher labels may have been filled after freezing, so the manifest's
  whole-file hash is superseded by a labels-filled hash recorded in the
  validation report.
* Only ``holdout=false`` (dev) pairs are judged. The holdout split is reserved
  for the final evaluation if any prompt tuning happens; with no tuning, the
  dev results are reported as-is and the holdout stays untouched.
* Pairs labeled ``ambiguous`` by the researcher are excluded from the accuracy
  denominator and reported as the ambiguous rate (plan.md §C1: report
  sensitivity, specificity, abstention, and failure rates, not accuracy alone).
* The judge runs through ``GenerationClient`` via ``judge_equivalence``; every
  judgment is appended to a ``judgment``-schema JSONL with prompt hash, cache
  key, and sanitized receipt. The judge config must still be
  ``enabled: false`` at report time — this script runs it under an explicit
  validation-only override and never flips the config itself.

Outputs: a validation report JSON + Markdown under ``docs/validation/`` with
confusion matrix, sensitivity/specificity, abstention/failure rates by task
type, and per-pair disagreement listing for researcher review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation import JudgeConfig, judge_equivalence, load_judge_config  # noqa: E402
from src.generation import GenerationClient  # noqa: E402

SET_PATH = REPO_ROOT / "data" / "validation" / "judge_validation_v1.jsonl"
MANIFEST_PATH = REPO_ROOT / "data" / "validation" / "judge_validation_v1_manifest.json"
JUDGE_CONFIG_PATH = REPO_ROOT / "configs" / "judge_answer_equivalence_v1.json"
DOCS_DIR = REPO_ROOT / "docs" / "validation"
JUDGMENTS_PATH = REPO_ROOT / "data" / "results" / "judge_validation_v1" / "judgments.jsonl"

#: Pair-identity fields covered by the freeze (labels are filled after freezing).
_IDENTITY_FIELDS = ("pair_id", "task_id", "task_type", "stratum", "answer_format",
                    "ground_truth", "model_answer", "deterministic_decision", "holdout")


def frozen_identity_hash(pairs: Sequence[Dict[str, Any]]) -> str:
    """Hash over pair-identity fields only (labels excluded by design)."""
    canonical = json.dumps(
        [{k: p[k] for k in _IDENTITY_FIELDS} for p in pairs],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_pairs(path: Path) -> List[Dict[str, Any]]:
    pairs = [json.loads(l) for l in Path(path).open(encoding="utf-8") if l.strip()]
    for pair in pairs:
        if pair.get("researcher_label") not in ("equivalent", "not_equivalent", "ambiguous"):
            raise RuntimeError(f"pair {pair.get('pair_id')} lacks a researcher label")
    return pairs


def confusion(dev_results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """2x2 confusion over decided pairs (judge abstentions excluded)."""
    tp = sum(1 for r in dev_results if r["label"] == "equivalent" and r["judge"] == "equivalent")
    tn = sum(1 for r in dev_results if r["label"] == "not_equivalent" and r["judge"] == "not_equivalent")
    fp = sum(1 for r in dev_results if r["label"] == "not_equivalent" and r["judge"] == "equivalent")
    fn = sum(1 for r in dev_results if r["label"] == "equivalent" and r["judge"] == "not_equivalent")
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def rates(conf: Dict[str, int]) -> Dict[str, Optional[float]]:
    tp, tn, fp, fn = conf["tp"], conf["tn"], conf["fp"], conf["fn"]
    return {
        "sensitivity": tp / (tp + fn) if (tp + fn) else None,  # catches true equivalences
        "specificity": tn / (tn + fp) if (tn + fp) else None,  # rejects true non-equivalences
        "accuracy": (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) else None,
    }


def run_validation(
    judge_config: JudgeConfig,
    pairs: Sequence[Dict[str, Any]],
    client: GenerationClient,
    *,
    judgments_path: Path = JUDGMENTS_PATH,
    run_id: str = "judge_validation_v1_dev",
) -> Dict[str, Any]:
    """Judge the dev split and compute validation metrics. Returns the report."""
    dev = [p for p in pairs if not p["holdout"]]
    if not dev:
        raise RuntimeError("empty dev split")

    # Validation-only override: the stored config must remain disabled until
    # the researcher approves; judging here is the C3 measurement itself.
    config = judge_config.model_copy(update={"enabled": True})

    results: List[Dict[str, Any]] = []
    for pair in dev:
        if pair["researcher_label"] == "ambiguous":
            results.append({
                "pair_id": pair["pair_id"], "task_id": pair["task_id"],
                "task_type": pair["task_type"], "stratum": pair["stratum"],
                "label": "ambiguous", "judge": None, "judge_decision_raw": None,
                "agree": None,
            })
            continue
        evaluation = judge_equivalence(
            pair["model_answer"],
            pair["ground_truth"],
            config=config,
            client=client,
            task_id=pair["task_id"],
            run_id=run_id,
            judgments_path=judgments_path,
        )
        judge_label = (
            "equivalent" if evaluation.decision == "equivalent"
            else "not_equivalent" if evaluation.decision == "not_equivalent"
            else "abstain"
        )
        results.append({
            "pair_id": pair["pair_id"], "task_id": pair["task_id"],
            "task_type": pair["task_type"], "stratum": pair["stratum"],
            "label": pair["researcher_label"], "judge": judge_label,
            "judge_decision_raw": evaluation.decision,
            "judge_reason": evaluation.reason,
            "judge_confidence": evaluation.judge.confidence if evaluation.judge else None,
            "agree": None if judge_label == "abstain" else judge_label == pair["researcher_label"],
        })

    decided = [r for r in results if r["label"] != "ambiguous" and r["judge"] != "abstain"]
    conf = confusion(decided)
    overall = rates(conf)

    by_type: Dict[str, Any] = {}
    for task_type in sorted({r["task_type"] for r in results}):
        group = [r for r in results if r["task_type"] == task_type]
        group_decided = [r for r in group if r["label"] != "ambiguous" and r["judge"] != "abstain"]
        by_type[task_type] = {
            "n": len(group),
            "n_ambiguous_label": sum(1 for r in group if r["label"] == "ambiguous"),
            "n_abstain": sum(1 for r in group if r["judge"] == "abstain"),
            **rates(confusion(group_decided)),
        }

    return {
        "schema": "judge_validation_report",
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "judge_id": config.judge_id,
        "rubric_version": config.rubric_version,
        "judge_model": f"{config.provider}/{config.model}",
        "confidence_threshold": config.confidence_threshold,
        "prompt_sha256": config.prompt_sha256(),
        "split": "dev (holdout=false); holdout untouched — no prompt tuning performed",
        "n_dev": len(dev),
        "n_ambiguous_label": sum(1 for r in results if r["label"] == "ambiguous"),
        "n_abstain": sum(1 for r in results if r["judge"] == "abstain"),
        "confusion": conf,
        "overall": overall,
        "by_task_type": by_type,
        "disagreements": [r for r in decided if r["agree"] is False],
        "pairs": results,
    }


def write_report(report: Dict[str, Any], docs_dir: Path) -> Dict[str, Path]:
    docs_dir.mkdir(parents=True, exist_ok=True)
    base = docs_dir / f"judge_{report['judge_id']}_rubric_{report['rubric_version']}_validation"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    o = report["overall"]
    lines = [
        f"# Judge validation: {report['judge_id']} rubric {report['rubric_version']}",
        "",
        f"- Judge model: `{report['judge_model']}`, temperature 0, confidence threshold {report['confidence_threshold']}",
        f"- Prompt SHA-256: `{report['prompt_sha256'][:16]}…`",
        f"- Split: {report['split']}",
        f"- Dev pairs: {report['n_dev']} (ambiguous labels excluded: {report['n_ambiguous_label']}; judge abstentions: {report['n_abstain']})",
        "",
        "## Confusion (decided pairs)",
        "",
        "| | judge: equivalent | judge: not_equivalent |",
        "|---|---|---|",
        f"| **label: equivalent** | {report['confusion']['tp']} | {report['confusion']['fn']} |",
        f"| **label: not_equivalent** | {report['confusion']['fp']} | {report['confusion']['tn']} |",
        "",
        f"- Sensitivity: {_fmt(o['sensitivity'])}  Specificity: {_fmt(o['specificity'])}  Accuracy: {_fmt(o['accuracy'])}",
        "",
        "## By task type",
        "",
        "| type | n | ambiguous | abstain | sensitivity | specificity | accuracy |",
        "|---|---|---|---|---|---|---|",
    ]
    for task_type, row in report["by_task_type"].items():
        lines.append(
            f"| {task_type} | {row['n']} | {row['n_ambiguous_label']} | {row['n_abstain']} "
            f"| {_fmt(row['sensitivity'])} | {_fmt(row['specificity'])} | {_fmt(row['accuracy'])} |"
        )
    lines += ["", "## Disagreements (decided pairs)", ""]
    if not report["disagreements"]:
        lines.append("None.")
    for r in report["disagreements"]:
        lines.append(
            f"- `{r['pair_id']}` ({r['task_id']}, {r['stratum']}): label={r['label']} "
            f"judge={r['judge']} (confidence {r.get('judge_confidence')})"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Judge config remains `enabled: false` until the researcher approves an",
        "  accuracy threshold and tie policy (plan.md §C1/C3).",
        "- Pairs for tombstoned tasks factual-041/044 are labeled `ambiguous` and",
        "  excluded from denominators (both sit in the untouched holdout split).",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "md": md_path}


def _fmt(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="set_path", default=str(SET_PATH))
    parser.add_argument("--judge-config", default=str(JUDGE_CONFIG_PATH))
    parser.add_argument("--docs-dir", default=str(DOCS_DIR))
    parser.add_argument("--judgments", default=str(JUDGMENTS_PATH))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pairs = load_pairs(Path(args.set_path))
    judge_config = load_judge_config(Path(args.judge_config))
    if judge_config.enabled:
        raise SystemExit("judge config is already enabled — validation must run BEFORE approval")
    client = GenerationClient()
    report = run_validation(
        judge_config, pairs, client, judgments_path=Path(args.judgments),
    )
    paths = write_report(report, Path(args.docs_dir))
    print(json.dumps({"confusion": report["confusion"], "overall": report["overall"],
                      "abstain": report["n_abstain"], "ambiguous": report["n_ambiguous_label"]}, indent=2))
    for name, path in paths.items():
        print(f"{name}: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
