"""C2 baseline runner: valid baseline generations against the canonical task set.

Rebuilt after the 2026-07-21 audit on the A3/A4/A5/A7 + B + C1 foundations.
Every run:

1. loads the canonical task set through the B1 discriminated-union schema
   (``src/task_schema.py``) and verifies the manifest hash (B);
2. gates the workhorse on valid, unexpired capability probes
   (``src/capabilities.require_valid``, A3);
3. verifies the task-set hash recorded in ``data/tasks/task_set_manifest.json``
   (researcher checkpoint);
4. estimates the whole batch in code (``src/costs.py``), atomically reserves it
   against ``budget_state.json`` (A4), and confirms with the researcher;
5. runs inside an append-only ``RunDirectory`` (A5) with a per-request state
   journal, sanitized raw receipts (A7), and manifest-based resume;
6. token-preflights each request against verified context windows (A3);
7. parses the constrained ``Final answer: X`` line and scores with the C1
   deterministic evaluator — judge-required answers are labeled ``unscored``,
   never incorrect and never silently judged;
8. reconciles the reservation to actual spend and writes a summary that
   distinguishes generation failure / truncation / parse failure / unscored /
   incorrect / correct (plan.md §C2). For ethics, agreement with the source
   label is reported as ``source_label_agreement``, never "accuracy".

Selection: ``--dry-run`` requires the explicit C2 pilot composition
(2 math / 1 logic / 1 factual / 1 ethics, one of which may be the preregistered
Hotpot context-length stress case). That 2/1/1/1 composition is intentional and
recorded in the run manifest; task-type performance must never be estimated
from it. The full run is all 200 canonical tasks.

Nothing here makes a network call by itself: ``run_baseline`` takes an injected
client, and the offline tests use a fake.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import budget_state, costs, preflight, task_schema  # noqa: E402
from src.capabilities import load_matrix, require_valid  # noqa: E402
from src.config import RunConfig, load_run_config  # noqa: E402
from src.evaluation import evaluate_answer, parse_final_answer  # noqa: E402
from src.generation import GenerationClient, GenerationRequest  # noqa: E402
from src.receipts import write_receipt  # noqa: E402
from src.run_manifest import RunDirectory, build_manifest  # noqa: E402

DEFAULT_TASKS_PATH = REPO_ROOT / "data" / "tasks" / "task_set.jsonl"
TASK_SET_MANIFEST_PATH = REPO_ROOT / "data" / "tasks" / "task_set_manifest.json"
CAPABILITY_MATRIX_PATH = REPO_ROOT / "data" / "capabilities.json"
DEFAULT_RESULTS_DIR = REPO_ROOT / "data" / "results"

#: Baseline generation is one call per task under one condition. The condition
#: id distinguishes baseline records from later corruption/hint conditions in
#: the shared (task_id, condition_id, mechanism, replicate_index) key space.
CONDITION_ID = "baseline"
PRICE_REVISION = "prices-2026-07-08"

#: Response categories for the C2 summary (plan.md §C2: "Baseline output must
#: distinguish generation failure, truncation, parse failure, unscored
#: equivalence, incorrect answer, and correct/equivalent answer").
CATEGORIES = (
    "generation_failure",
    "truncation",
    "parse_failure",
    "unscored",
    "incorrect",
    "correct",
)

#: The intentional dry-run composition (plan.md §C2 step 1). Never estimate
#: task-type performance from it.
DRY_RUN_COUNTS = {"math": 2, "logic": 1, "factual": 1, "ethics": 1}


# --------------------------------------------------------------------------- #
# Task loading / selection
# --------------------------------------------------------------------------- #


def load_tasks(path: Path) -> List[task_schema.TaskRecord]:
    """Load the canonical task set through the B1 schema (fail closed)."""
    tasks: List[task_schema.TaskRecord] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                tasks.append(task_schema.parse_task(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"invalid task record on line {lineno} of {path}: {exc}") from exc
    return tasks


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_task_set_hash(tasks_path: Path, manifest_path: Path) -> str:
    """Verify the task-set file against the B7 manifest; return its sha256.

    Fail-closed (ground rule 5 / researcher checkpoint): a missing manifest or
    a hash mismatch stops the run before any paid call.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    recorded = manifest.get("task_set_sha256")
    actual = sha256_file(tasks_path)
    if not recorded:
        raise RuntimeError(f"no task_set_sha256 recorded in {manifest_path}")
    if recorded != actual:
        raise RuntimeError(
            f"task set hash mismatch: {tasks_path} is {actual[:12]}… but the "
            f"approved manifest records {recorded[:12]}…. Freeze/approval "
            "violation — stop and reconcile with the researcher."
        )
    return actual


def select_dry_run_pilot(
    tasks: Sequence[task_schema.TaskRecord], stress_task_id: Optional[str] = None
) -> List[task_schema.TaskRecord]:
    """Deterministic C2 dry-run pilot: first N approved tasks per type at the
    2/1/1/1 composition, optionally pinning the Hotpot stress case."""
    selected: List[task_schema.TaskRecord] = []
    for task_type, count in DRY_RUN_COUNTS.items():
        pool = [t for t in tasks if t.task_type == task_type]
        if stress_task_id and task_type == "factual":
            stress = [t for t in pool if t.id == stress_task_id]
            if not stress:
                raise ValueError(f"stress task {stress_task_id!r} not found among factual tasks")
            rest = [t for t in pool if t.id != stress_task_id]
            pool = stress + rest
        if len(pool) < count:
            raise ValueError(f"not enough {task_type} tasks for the dry-run pilot")
        selected.extend(pool[:count])
    return selected


# --------------------------------------------------------------------------- #
# Request building / outcome categorization
# --------------------------------------------------------------------------- #


def build_request(task: task_schema.TaskRecord, config: RunConfig, force_fresh: bool) -> GenerationRequest:
    prompt = config.prompt_template.format(problem=task.problem)
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


def categorize(
    *,
    generation_error: Optional[str],
    finish_reason: Optional[str],
    final_answer: Optional[str],
    decision: Optional[str],
) -> str:
    """Map one response to exactly one C2 summary category."""
    if generation_error is not None:
        return "generation_failure"
    if finish_reason in ("length", "max_tokens"):
        return "truncation"
    if final_answer is None:
        return "parse_failure"
    if decision == "unscored":
        return "unscored"
    if decision == "equivalent":
        return "correct"
    return "incorrect"


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #


def run_baseline(
    config_path: Path,
    tasks_path: Path,
    client: GenerationClient,
    *,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    budget_path: Path = budget_state.DEFAULT_STATE_PATH,
    log_path: Path = costs.DEFAULT_LOG_PATH,
    task_set_manifest_path: Path = TASK_SET_MANIFEST_PATH,
    capability_matrix_path: Path = CAPABILITY_MATRIX_PATH,
    task_ids: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    stress_task_id: Optional[str] = None,
    force_fresh: bool = False,
    confirmed: bool = False,
    interactive: bool = True,
    code_revision: Optional[str] = None,
    run_id_suffix: str = "",
) -> Dict[str, Any]:
    """Run a baseline generation set end-to-end and return the summary dict.

    Injectable paths/client keep this fully offline-testable. ``confirmed``
    asserts the researcher has seen and approved the estimate (ground rule 2);
    with ``interactive`` the estimate prompts instead.
    """
    config = load_run_config(config_path)
    run_id = config.run_id + run_id_suffix

    # B: schema-validated tasks + researcher-approved hash.
    tasks = load_tasks(tasks_path)
    task_set_sha = verify_task_set_hash(tasks_path, task_set_manifest_path)

    # A3: the model's required capabilities must be probe-valid and unexpired.
    # Baselines rely on hidden thinking being genuinely disabled (or known to
    # be unsupported) so the prompted CoT is the whole reasoning channel.
    if config.thinking_setting != "none_supported":
        probes = load_matrix(capability_matrix_path)
        match = next(
            (
                p
                for p in probes.values()
                if p.provider == config.provider
                and p.model == config.model
                and p.capability == "thinking_disabled"
            ),
            None,
        )
        require_valid(match, today=date.today())

    # Selection.
    if dry_run:
        selected = select_dry_run_pilot(tasks, stress_task_id=stress_task_id)
        selection_args: Dict[str, Any] = {
            "mode": "dry_run",
            "composition": dict(DRY_RUN_COUNTS),
            "stress_task_id": stress_task_id,
            "note": "intentional 2/1/1/1 pilot composition; never estimate "
            "task-type performance from it",
        }
    else:
        selected = list(tasks)
        selection_args = {"mode": "full", "task_type": "all"}
    if task_ids is not None:
        wanted = set(task_ids)
        selected = [t for t in selected if t.id in wanted]
        selection_args["task_ids"] = sorted(wanted)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        selected = selected[:limit]
        selection_args["limit"] = limit
    if not selected:
        raise ValueError("selection is empty")

    requests = [build_request(t, config, force_fresh) for t in selected]

    # A4: estimate + reserve before any paid call.
    est = costs.estimate_batch(requests, confirmed=confirmed, interactive=interactive, verbose=True)
    reservation_id = f"{run_id}-baseline"
    budget_state.reserve(
        reservation_id,
        max(est.total_usd, 1e-6),
        run_id,
        path=budget_path,
        note=f"baseline {len(requests)} calls",
    )

    manifest = build_manifest(
        run_id=run_id,
        experiment_kind="baseline",
        task_set_path=str(tasks_path.relative_to(REPO_ROOT)) if tasks_path.is_relative_to(REPO_ROOT) else str(tasks_path),
        task_set_sha256=task_set_sha,
        selected_task_ids=[t.id for t in selected],
        selection_args=selection_args,
        config_sha256=hashlib.sha256(
            json.dumps(config.model_dump(), sort_keys=True, default=str).encode()
        ).hexdigest(),
        prompt_template_version=config.prompt_template_version,
        requested_settings={
            "thinking_setting": config.thinking_setting,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "seed": config.seed,
            "prefill_mechanism": config.prefill_mechanism,
        },
        price_revision=PRICE_REVISION,
        provider_route=config.provider_route,
        code_revision=code_revision,
        root=REPO_ROOT,
    )
    run_dir_path = results_dir / run_id
    if (run_dir_path / "manifest.json").exists():
        rd = RunDirectory.resume(run_dir_path, manifest)
    else:
        rd = RunDirectory.create(
            run_dir_path,
            manifest,
            config_text=config_path.read_text(encoding="utf-8"),
        )

    actual_spend = 0.0
    counts: Dict[str, Dict[str, int]] = {}
    failures: List[Dict[str, Any]] = []
    completed = skipped_duplicates = 0
    try:
        with rd.lease():
            for task, request in zip(selected, requests):
                key_record = {"task_id": task.id, "condition_id": CONDITION_ID, "mechanism": config.prefill_mechanism}
                response_key = rd.response_key(key_record)

                generation_error: Optional[str] = None
                result = None
                rd.record_transition(response_key, "planned")
                try:
                    preflight.preflight_request(request)
                except preflight.UnknownContextWindowError:
                    pass  # window UNVERIFIED: recorded, not silently assumed

                rd.record_transition(response_key, "submitted")
                try:
                    result = client.generate(request)
                except Exception as exc:  # fail honestly, keep going
                    generation_error = f"{type(exc).__name__}: {exc}"
                if result is not None:
                    rd.record_transition(response_key, "received")

                receipt_rel: Optional[str] = None
                if result is not None and result.raw_response is not None:
                    receipt_path = rd.raw_dir / f"{task.id}__{CONDITION_ID}.json"
                    write_receipt(receipt_path, result.raw_response)
                    try:
                        receipt_rel = str(receipt_path.relative_to(REPO_ROOT))
                    except ValueError:
                        receipt_rel = str(receipt_path)

                final_answer = parse_final_answer(result.text) if result is not None else None
                evaluation = (
                    evaluate_answer(final_answer, task.ground_truth, task.answer_format)
                    if final_answer is not None
                    else None
                )
                category = categorize(
                    generation_error=generation_error,
                    finish_reason=result.finish_reason if result is not None else None,
                    final_answer=final_answer,
                    decision=evaluation.decision if evaluation is not None else None,
                )

                call_cost = 0.0
                if result is not None:
                    try:
                        call_cost = costs.incremental_cost_for_result(config.model, result)
                    except costs.UnverifiedPriceError:
                        call_cost = 0.0
                    actual_spend += call_cost
                    if call_cost and not result.cache_hit:
                        costs.log_cost(
                            run_id=run_id,
                            model=config.model,
                            provider=config.provider,
                            input_tokens=result.input_tokens or 0,
                            output_tokens=result.output_tokens or 0,
                            cost_usd=call_cost,
                            cached_tokens=result.cached_input_tokens,
                            log_path=log_path,
                            emit=False,
                        )

                record = {
                    "task_id": task.id,
                    "condition_id": CONDITION_ID,
                    "mechanism": config.prefill_mechanism,
                    "replicate_index": 0,
                    "task_type": task.task_type,
                    "answer_format": task.answer_format,
                    "scoring_policy": task.scoring_policy,
                    "ground_truth": task.ground_truth,
                    "text": result.text if result is not None else None,
                    "final_answer": final_answer,
                    "category": category,
                    "evaluation": (
                        {
                            "decision": evaluation.decision,
                            "method": evaluation.method,
                            "normalized_prediction": evaluation.normalized_prediction,
                            "normalized_ground_truth": evaluation.normalized_ground_truth,
                            "reason": evaluation.reason,
                            "alias_matched": evaluation.alias_matched,
                        }
                        if evaluation is not None
                        else None
                    ),
                    "generation_error": generation_error,
                    "cache_key": result.cache_key if result is not None else None,
                    "cache_hit": result.cache_hit if result is not None else None,
                    "timestamp": result.timestamp if result is not None else None,
                    "system_fingerprint": result.system_fingerprint if result is not None else None,
                    "finish_reason": result.finish_reason if result is not None else None,
                    "reasoning_tokens": result.reasoning_tokens if result is not None else None,
                    "applied_settings": result.applied_settings if result is not None else None,
                    "idempotency_key": result.idempotency_key if result is not None else None,
                    "usage": (
                        {
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                            "cached_input_tokens": result.cached_input_tokens,
                        }
                        if result is not None
                        else None
                    ),
                    "cost_usd": call_cost,
                    "raw_response_path": receipt_rel,
                }
                status = rd.append_response(record)
                if status == "duplicate_ok":
                    skipped_duplicates += 1
                else:
                    completed += 1
                rd.record_transition(response_key, "persisted")
                rd.record_transition(response_key, "accounted")

                bucket = counts.setdefault(task.task_type, {c: 0 for c in CATEGORIES})
                bucket[category] += 1
                if category in ("generation_failure", "truncation", "parse_failure"):
                    failures.append(
                        {"task_id": task.id, "category": category, "detail": generation_error}
                    )

        # A4: reconcile reservation to actual spend.
        budget_state.reconcile(reservation_id, actual_spend, path=budget_path)
    except Exception:
        try:
            budget_state.release(reservation_id, path=budget_path)
        except Exception:
            pass
        raise

    summary = _build_summary(
        run_id=run_id,
        config=config,
        selected=selected,
        counts=counts,
        dry_run=dry_run,
        actual_spend=actual_spend,
        estimated_usd=est.total_usd,
        completed=completed,
        skipped_duplicates=skipped_duplicates,
        failures=failures,
    )
    rd.write_summary(summary)
    return summary


def _build_summary(
    *,
    run_id: str,
    config: RunConfig,
    selected: Sequence[task_schema.TaskRecord],
    counts: Dict[str, Dict[str, int]],
    dry_run: bool,
    actual_spend: float,
    estimated_usd: float,
    completed: int,
    skipped_duplicates: int,
    failures: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pure summary builder: categories per task type, ethics reported as
    source-label agreement — never accuracy (plan.md §C2 step 4)."""
    per_type: Dict[str, Any] = {}
    for task_type in sorted(counts):
        bucket = counts[task_type]
        decided = bucket["correct"] + bucket["incorrect"]
        row: Dict[str, Any] = {
            "n_tasks": sum(bucket.values()),
            "categories": bucket,
            "n_parse_failures": bucket["parse_failure"],
            "n_unscored": bucket["unscored"],
        }
        if task_type == "ethics":
            # Source labels are consensus annotations, not objective moral
            # truth: report agreement, never "accuracy".
            row["source_label_agreement"] = (
                bucket["correct"] / decided if decided else None
            )
        else:
            row["accuracy_excluding_unscored"] = (
                bucket["correct"] / decided if decided else None
            )
        per_type[task_type] = row

    return {
        "run_id": run_id,
        "provider": config.provider,
        "model": config.model,
        "experiment_kind": "baseline",
        "dry_run": dry_run,
        "dry_run_note": (
            "intentional 2/1/1/1 pilot composition; never estimate task-type "
            "performance from it"
        )
        if dry_run
        else None,
        "n_selected": len(selected),
        "n_completed": completed,
        "n_skipped_duplicates": skipped_duplicates,
        "per_task_type": per_type,
        "scoring_policy": (
            "C1 deterministic only (numeric|yes_no|normalized_text+aliases); "
            "judge-required answers labeled unscored, never silently judged"
        ),
        "estimated_usd": estimated_usd,
        "actual_usd": round(actual_spend, 8),
        "failure_samples": list(failures)[:20],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="Run config YAML.")
    parser.add_argument("--tasks-path", default=str(DEFAULT_TASKS_PATH))
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--dry-run", action="store_true",
                        help="C2 pilot: 2 math/1 logic/1 factual/1 ethics.")
    parser.add_argument("--stress-task-id", default=None,
                        help="Pin the preregistered Hotpot context-length stress case (dry run).")
    parser.add_argument("--task-id", dest="task_ids", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force-fresh", action="store_true")
    parser.add_argument("--run-id-suffix", default="",
                        help="Suffix appended to the config run_id (a distinct selection "
                             "needs a distinct run directory, e.g. '_dry' / '_full').")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm the estimate non-interactively (still reserved + logged).")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    client = GenerationClient()
    summary = run_baseline(
        Path(args.config),
        Path(args.tasks_path),
        client,
        results_dir=Path(args.results_dir),
        task_ids=args.task_ids,
        limit=args.limit,
        dry_run=args.dry_run,
        stress_task_id=args.stress_task_id,
        force_fresh=args.force_fresh,
        run_id_suffix=args.run_id_suffix,
        confirmed=args.yes,
        interactive=not args.yes,
    )
    print(json.dumps(summary["per_task_type"], indent=2))
    print(f"actual spend: ${summary['actual_usd']:.6f} (estimate ${summary['estimated_usd']:.6f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
