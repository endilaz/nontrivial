"""GLM-only SECONDARY reveal analysis: judge the hidden reasoning trace (2026-07-26).

The canonical F reveal metric judges the VISIBLE answer text (content-only),
comparable across the panel. For GLM-5.2 (hidden-reasoning stratum), disclosure
plausibly lives in the native reasoning trace. This script re-judges every
shifted GLM transcript with the SAME approved judge (v1.1.1) but over
``reasoning + content`` — a DIFFERENT CONSTRUCT from the canonical metric:
never pool or compare directly against content-only rates without the
stratification caveat (researcher decision 2026-07-26: report both).

Judge re-validation for this construct (researcher labels on reasoning-trace
samples) is a separate follow-up; rates here are preliminary until then.

Append-only: judgments go to a separate JSONL (``_reasoning`` suffix) and the
addendum is a NEW sibling file (``reveal_judged_reasoning.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.admission import judge_admission, load_admission_judge_config  # noqa: E402
from src.generation import GenerationClient  # noqa: E402

DEFAULT_JUDGE_CONFIG = REPO_ROOT / "configs" / "judge_admission_v1_1_1.json"
JUDGE_RUNS_DIR = REPO_ROOT / "data" / "results" / "judge_runs"
ADDENDUM_OUT = "reveal_judged_reasoning.json"
CUED_CONDITIONS = ("suggested_cue", "authority_cue", "metadata_cue", "grader_cue")


def reasoning_plus_content(record: Dict[str, Any], run_dir: Path) -> str:
    """Concatenate the raw reasoning trace and the visible answer."""
    raw_path = record.get("raw_response_path")
    reasoning = ""
    if raw_path:
        raw = json.loads((REPO_ROOT / raw_path).read_text(encoding="utf-8"))
        msg = (raw.get("choices") or [{}])[0].get("message") or {}
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    content = record.get("text") or ""
    return f"{reasoning}\n\n{content}".strip()


def score_run_reasoning(
    run_dir: Path,
    judge_config_path: Path,
    client: GenerationClient,
    *,
    judgments_path: Path,
    log_path: Path,
) -> Dict[str, Any]:
    config = load_admission_judge_config(judge_config_path)
    records = [
        json.loads(l)
        for l in (run_dir / "responses.jsonl").open(encoding="utf-8")
        if l.strip()
    ]

    per_condition: Dict[str, Counter] = {c: Counter() for c in CUED_CONDITIONS}
    per_type_condition: Dict[str, Dict[str, Counter]] = {}
    judged = skipped_no_reasoning = 0
    for record in records:
        condition_id = record.get("condition_id")
        if condition_id not in CUED_CONDITIONS:
            continue
        cue = record.get("cue") or {}
        if not cue.get("ok"):
            continue
        if (record.get("cue_evaluation") or {}).get("decision") != "equivalent":
            continue
        text = reasoning_plus_content(record, run_dir)
        if not text:
            skipped_no_reasoning += 1
            continue
        result = judge_admission(
            text,
            cue.get("target") or "",
            config=config,
            client=client,
            task_id=record.get("task_id"),
            condition_id=condition_id,
            run_id=f"{run_dir.name}__reasoning",
            judgments_path=judgments_path,
            log_path=log_path,
        )
        judged += 1
        task_type = record.get("task_type")
        bucket = per_type_condition.setdefault(task_type, {c: Counter() for c in CUED_CONDITIONS})
        for ctr in (per_condition[condition_id], bucket[condition_id]):
            ctr["shifted"] += 1
            if result.disclosure in ("explicit", "indirect"):
                ctr["revealed"] += 1
            ctr[f"label_{result.disclosure}"] += 1

    def rates(ctr: Counter) -> Dict[str, Any]:
        shifted = ctr["shifted"]
        return {
            "n_shifted": shifted,
            "n_revealed": ctr["revealed"],
            "reveal_rate": (ctr["revealed"] / shifted) if shifted else None,
            "labels": {k[6:]: v for k, v in ctr.items() if k.startswith("label_")},
        }

    return {
        "run_id": run_dir.name,
        "judge_id": config.judge_id,
        "rubric_version": config.rubric_version,
        "judge_config": str(judge_config_path.relative_to(REPO_ROOT)),
        "method": (
            "llm_admission_judge over reasoning trace + visible content "
            "(GLM-only SECONDARY construct, researcher decision 2026-07-26); "
            "NOT comparable to content-only canonical rates without "
            "stratification caveat; judge not yet re-validated on reasoning traces"
        ),
        "n_judged": judged,
        "n_skipped_no_text": skipped_no_reasoning,
        "per_condition": {c: rates(per_condition[c]) for c in CUED_CONDITIONS},
        "per_task_type": {
            tt: {c: rates(per_type_condition[tt][c]) for c in CUED_CONDITIONS}
            for tt in sorted(per_type_condition)
        },
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--judge-config", default=str(DEFAULT_JUDGE_CONFIG))
    parser.add_argument("--judgments-path", default=None)
    parser.add_argument("--log-path", default=str(REPO_ROOT / "logs" / "cost_log.csv"))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_dir = Path(args.run_dir)
    judgments_path = (
        Path(args.judgments_path)
        if args.judgments_path
        else JUDGE_RUNS_DIR / f"admission_{run_dir.name}_reasoning.jsonl"
    )
    client = GenerationClient()
    addendum = score_run_reasoning(
        run_dir, Path(args.judge_config), client,
        judgments_path=judgments_path, log_path=Path(args.log_path),
    )
    out_path = run_dir / ADDENDUM_OUT
    out_path.write_text(json.dumps(addendum, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(addendum["per_condition"], indent=2))
    print(f"judged: {addendum['n_judged']} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
