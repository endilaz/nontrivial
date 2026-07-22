"""Run Phase 3 baseline generations and record per-model accuracy by task type.

This script reuses the existing generation, config, and cost plumbing rather
than creating a parallel execution path. For each supplied run config, it:

1. loads the curated task set from ``data/tasks/task_set.jsonl``;
2. estimates the batch cost in code before any API calls;
3. runs one baseline generation per task;
4. parses the constrained ``Final answer: X`` line;
5. scores the answer with a conservative deterministic comparator; and
6. writes both per-call JSONL records and per-task-type summary files.

Scoring policy
--------------
This intentionally does NOT implement the still-unvalidated LLM equivalence
judge from the project plan. It only marks an answer correct when one of the
currently-supported deterministic checks succeeds:

* ``number``: exact numeric equality after canonical numeric parsing
* ``yes_no``: normalized yes/no equality
* ``short_text``: normalized exact string equality, with an automatic numeric
  fallback when both strings are purely numeric-looking

That makes the baseline summaries conservative but auditable.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# Make `import src.*` work when invoked as `python scripts/baseline_accuracy.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import costs  # noqa: E402
from src.config import RunConfig, load_run_config, save_run_config  # noqa: E402
from src.evaluation import parse_final_answer  # noqa: E402
from src.generation import GenerationClient, GenerationRequest  # noqa: E402

DEFAULT_TASKS_PATH = REPO_ROOT / "data" / "tasks" / "task_set.jsonl"
DEFAULT_RESULTS_DIR = REPO_ROOT / "data" / "results"
ENV_PATH = REPO_ROOT / ".env"

REQUIRED_TASK_FIELDS = {
    "id",
    "task_type",
    "problem",
    "ground_truth",
    "answer_format",
}

NUMERIC_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
SURROUNDING_PUNCTUATION_RE = re.compile(r'^[\s"\'“”‘’`*_~()\[\]{}<>]+|[\s"\'“”‘’`*_~()\[\]{}<>.,;:!?]+$')


@dataclass(frozen=True)
class ScoreResult:
    is_correct: bool
    method: str
    normalized_prediction: Optional[str]
    normalized_ground_truth: Optional[str]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run baseline generations from one or more run configs and record "
            "per-model baseline accuracy per task type."
        )
    )
    parser.add_argument(
        "--config",
        dest="config_paths",
        action="append",
        required=True,
        help="Path to a YAML run config. Pass once per model/run.",
    )
    parser.add_argument(
        "--tasks-path",
        default=str(DEFAULT_TASKS_PATH),
        help="Task-set JSONL path. Defaults to data/tasks/task_set.jsonl.",
    )
    parser.add_argument(
        "--results-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help="Directory for per-run JSONL/config/summary outputs.",
    )
    parser.add_argument(
        "--task-type",
        action="append",
        choices=["math", "logic", "reasoning", "ethics"],
        help="Restrict the run to one or more task types.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N tasks after filtering.",
    )
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Bypass cache reads for generation requests.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Proceed without an interactive confirmation prompt if the estimate exceeds $5.",
    )
    return parser.parse_args(argv)


def load_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            task = json.loads(line)
            missing = REQUIRED_TASK_FIELDS.difference(task)
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(
                    f"Task on line {line_number} in {path} is missing required fields: {missing_list}"
                )
            tasks.append(task)
    return tasks


def filter_tasks(
    tasks: Iterable[dict[str, Any]],
    task_types: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    filtered = list(tasks)
    if task_types:
        wanted = set(task_types)
        filtered = [task for task in filtered if task["task_type"] in wanted]
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1 when provided")
        filtered = filtered[:limit]
    return filtered


def _parse_numeric(text: Optional[str]) -> Optional[Decimal]:
    if text is None:
        return None
    candidate = text.strip()
    if not candidate or not NUMERIC_RE.fullmatch(candidate):
        return None
    candidate = candidate.replace(",", "")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _normalize_string(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    normalized = " ".join(text.strip().split())
    normalized = SURROUNDING_PUNCTUATION_RE.sub("", normalized)
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    return normalized.casefold()


def _normalize_yes_no(text: Optional[str]) -> Optional[str]:
    normalized = _normalize_string(text)
    if normalized in {"yes", "no"}:
        return normalized
    return None


def score_answer(
    final_answer: Optional[str],
    ground_truth: str,
    answer_format: str,
) -> ScoreResult:
    predicted_number = _parse_numeric(final_answer)
    truth_number = _parse_numeric(ground_truth)
    if answer_format == "number" or (
        predicted_number is not None and truth_number is not None
    ):
        predicted_text = None if predicted_number is None else format(predicted_number, "f")
        truth_text = None if truth_number is None else format(truth_number, "f")
        return ScoreResult(
            is_correct=(
                predicted_number is not None
                and truth_number is not None
                and predicted_number == truth_number
            ),
            method="numeric_exact",
            normalized_prediction=predicted_text,
            normalized_ground_truth=truth_text,
        )

    if answer_format == "yes_no":
        predicted_yes_no = _normalize_yes_no(final_answer)
        truth_yes_no = _normalize_yes_no(ground_truth)
        return ScoreResult(
            is_correct=(
                predicted_yes_no is not None
                and truth_yes_no is not None
                and predicted_yes_no == truth_yes_no
            ),
            method="yes_no_exact",
            normalized_prediction=predicted_yes_no,
            normalized_ground_truth=truth_yes_no,
        )

    predicted_text = _normalize_string(final_answer)
    truth_text = _normalize_string(ground_truth)
    return ScoreResult(
        is_correct=(
            predicted_text is not None
            and truth_text is not None
            and predicted_text == truth_text
        ),
        method="normalized_string_exact",
        normalized_prediction=predicted_text,
        normalized_ground_truth=truth_text,
    )


def build_request(task: dict[str, Any], config: RunConfig, force_fresh: bool) -> GenerationRequest:
    prompt = config.prompt_template.format(problem=task["problem"])
    return GenerationRequest(
        provider=config.provider,
        model=config.model,
        provider_route=config.provider_route,
        messages=[{"role": "user", "content": prompt}],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        seed=config.seed,
        thinking_setting=config.thinking_setting,
        force_fresh=force_fresh,
        prompt_template_version=config.prompt_template_version,
    )


def per_run_paths(results_dir: Path, run_id: str) -> dict[str, Path]:
    return {
        "results_jsonl": results_dir / f"{run_id}.baseline_generations.jsonl",
        "config_copy": results_dir / f"{run_id}.baseline_generations.config.yaml",
        "summary_json": results_dir / f"{run_id}.baseline_accuracy_by_task_type.json",
        "summary_csv": results_dir / f"{run_id}.baseline_accuracy_by_task_type.csv",
    }


def write_summary_csv(rows: Sequence[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "run_id",
        "provider",
        "model",
        "task_type",
        "n_tasks",
        "n_correct",
        "accuracy",
        "n_parse_failures",
        "n_cache_hits",
        "total_cost_usd",
        "scoring_policy",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def summarize_run(
    records: Sequence[dict[str, Any]],
    config: RunConfig,
    tasks_path: Path,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["task_type"], []).append(record)

    summary_rows: list[dict[str, Any]] = []
    for task_type in sorted(grouped):
        bucket = grouped[task_type]
        n_tasks = len(bucket)
        n_correct = sum(1 for record in bucket if record["is_correct"])
        n_parse_failures = sum(1 for record in bucket if record["final_answer"] is None)
        n_cache_hits = sum(1 for record in bucket if record["cache_hit"])
        total_cost_usd = sum(record["cost_usd"] or 0.0 for record in bucket)
        summary_rows.append(
            {
                "run_id": config.run_id,
                "provider": config.provider,
                "model": config.model,
                "task_type": task_type,
                "n_tasks": n_tasks,
                "n_correct": n_correct,
                "accuracy": n_correct / n_tasks if n_tasks else None,
                "n_parse_failures": n_parse_failures,
                "n_cache_hits": n_cache_hits,
                "total_cost_usd": round(total_cost_usd, 8),
                "scoring_policy": (
                    "deterministic_only:number|yes_no|normalized_string_exact; "
                    "no LLM equivalence judge"
                ),
                "tasks_path": str(tasks_path),
            }
        )
    return summary_rows


def run_one_config(
    config_path: Path,
    tasks: Sequence[dict[str, Any]],
    tasks_path: Path,
    results_dir: Path,
    force_fresh: bool,
    confirmed: bool,
    client: GenerationClient,
) -> list[dict[str, Any]]:
    config = load_run_config(config_path)
    requests = [build_request(task, config, force_fresh=force_fresh) for task in tasks]

    try:
        costs.estimate_batch(
            requests,
            confirmed=confirmed,
            interactive=True,
            verbose=True,
        )
    except costs.ConfirmationRequired:
        raise
    except costs.UnverifiedPriceError as exc:
        raise RuntimeError(
            f"Cannot run {config.run_id} ({config.provider}/{config.model}): {exc}"
        ) from exc

    paths = per_run_paths(results_dir, config.run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    save_run_config(config, paths["config_copy"])

    records: list[dict[str, Any]] = []
    with paths["results_jsonl"].open("w", encoding="utf-8") as fh:
        for task, request in zip(tasks, requests):
            prompt = request.messages[0]["content"]
            result = client.generate(request)
            final_answer = parse_final_answer(result.text)
            score = score_answer(final_answer, str(task["ground_truth"]), str(task["answer_format"]))

            cost_usd = None
            cost_note = None
            try:
                cost_usd = costs.incremental_cost_for_result(config.model, result)
            except costs.UnverifiedPriceError as exc:
                cost_note = str(exc)

            if cost_usd is not None and not result.cache_hit:
                costs.log_cost(
                    run_id=config.run_id,
                    model=config.model,
                    provider=config.provider,
                    input_tokens=result.input_tokens or 0,
                    output_tokens=result.output_tokens or 0,
                    cost_usd=cost_usd,
                    cached_tokens=result.cached_input_tokens,
                )

            record = {
                "id": task["id"],
                "task_type": task["task_type"],
                "problem": task["problem"],
                "ground_truth": task["ground_truth"],
                "answer_format": task["answer_format"],
                "prompt": prompt,
                "response_text": result.text,
                "final_answer": final_answer,
                "is_correct": score.is_correct,
                "scoring_method": score.method,
                "normalized_prediction": score.normalized_prediction,
                "normalized_ground_truth": score.normalized_ground_truth,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cached_input_tokens": result.cached_input_tokens,
                    "reasoning_tokens": result.reasoning_tokens,
                },
                "cost_usd": cost_usd,
                "cost_note": cost_note,
                "cache_hit": result.cache_hit,
                "cache_key": result.cache_key,
                "provider_timestamp": result.timestamp,
                "system_fingerprint": result.system_fingerprint,
                "finish_reason": result.finish_reason,
                "prefill_mechanism": result.prefill_mechanism,
                "applied_settings": result.applied_settings,
                "config": config.model_dump(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            records.append(record)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

            print(
                f"[{config.run_id}][{task['id']}] final_answer={final_answer!r} "
                f"correct={score.is_correct} cache_hit={result.cache_hit} cost={cost_usd}"
            )

    summary_rows = summarize_run(records, config, tasks_path)
    summary_payload = {
        "run_id": config.run_id,
        "provider": config.provider,
        "model": config.model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_path": str(paths["results_jsonl"]),
        "config_copy_path": str(paths["config_copy"]),
        "summary_rows": summary_rows,
    }
    paths["summary_json"].write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary_csv(summary_rows, paths["summary_csv"])

    print(f"Wrote baseline generations to {paths['results_jsonl']}")
    print(f"Wrote per-task-type summary to {paths['summary_json']}")
    return summary_rows


def write_combined_summary(all_rows: Sequence[dict[str, Any]], results_dir: Path) -> None:
    json_path = results_dir / "baseline_accuracy_summary.json"
    csv_path = results_dir / "baseline_accuracy_summary.csv"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": list(all_rows),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary_csv(all_rows, csv_path)
    print(f"Wrote combined summary to {json_path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if not ENV_PATH.exists():
        print(
            "ERROR: .env not found at "
            f"{ENV_PATH}\n"
            "This runner makes real API calls and requires keys. Copy the\n"
            "template first:  cp .env.template .env   then fill in your keys.\n"
            "Refusing to run.",
            file=sys.stderr,
        )
        return 1

    tasks_path = Path(args.tasks_path)
    tasks = filter_tasks(load_tasks(tasks_path), task_types=args.task_type, limit=args.limit)
    if not tasks:
        print("ERROR: no tasks selected after filtering.", file=sys.stderr)
        return 1

    client = GenerationClient()
    results_dir = Path(args.results_dir)
    all_rows: list[dict[str, Any]] = []

    for config_path_str in args.config_paths:
        config_path = Path(config_path_str)
        summary_rows = run_one_config(
            config_path=config_path,
            tasks=tasks,
            tasks_path=tasks_path,
            results_dir=results_dir,
            force_fresh=args.force_fresh,
            confirmed=args.yes,
            client=client,
        )
        all_rows.extend(summary_rows)

    write_combined_summary(all_rows, results_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())