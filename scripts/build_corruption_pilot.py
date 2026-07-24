#!/usr/bin/env python3
"""Build the frozen E3 corruption pilot sample (spec §7.2 gate).

Selects a deterministic, stratified sample of baseline CoTs from the
workhorse full baseline and materializes every corruption condition for
reviewer inspection, BEFORE any full corruption generation. This is the E3
quality gate: the researcher reviews representative samples from every
task-type x corruption-type x position cell.

Offline, deterministic, no model calls. Output:

- ``data/validation/corruption_pilot_v1.jsonl`` — one JSON per pilot task with
  the full condition set (records + derived corrupted CoTs).
- ``data/validation/corruption_pilot_v1.md`` — human-readable review packet:
  per cell, the original step vs corrupted step and the full corrupted CoT.

Sample design (frozen here, not tuned on outcomes): up to 3 tasks per task
type, chosen as the first N canonical task-ids (sorted) whose baseline
response parses cleanly and whose frozen positions are all distinct
(n_steps >= 4) where possible — short CoTs are INCLUDED deliberately for
ethics so the short-CoT N/A policy is visible in review. Selection is by
task-id order only; no content-based cherry-picking.

The factual rows come from the canonical baseline run
(``baseline_deepseek_v4_flash_v2_full``); factual-041/044 are excluded
automatically because they are not in the canonical task set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.corruption import build_records  # noqa: E402
from src.corruption_dispatch import TaskContext, apply_semantic_edits  # noqa: E402
from src.corruption_operators import extract_logic_categories  # noqa: E402
from src.parsing import parse_cot  # noqa: E402
from src.task_schema import load_task_set  # noqa: E402

TASK_SET = REPO / "data/tasks/task_set.jsonl"
BASELINE = REPO / "data/results/baseline_deepseek_v4_flash_v2_full/responses.jsonl"
OUT_JSONL = REPO / "data/validation/corruption_pilot_v1.jsonl"
OUT_MD = REPO / "data/validation/corruption_pilot_v1.md"

PILOT_PER_TYPE = 3
PILOT_SAMPLE_VERSION = "corruption_pilot_v1"


def _task_context(task) -> TaskContext:
    if task.task_type == "logic":
        return TaskContext(
            "logic", task.ground_truth, extract_logic_categories(task.problem)
        )
    if task.task_type == "factual":
        return TaskContext(
            "factual",
            task.ground_truth,
            [e.title for e in task.factual.context],
            [sf.title for sf in task.factual.supporting_facts],
        )
    return TaskContext(task.task_type, task.ground_truth)


def _select_pilot(tasks, responses) -> list:
    """First N canonical ids per type with a parseable baseline response."""
    selected = []
    per_type: dict[str, int] = {}
    for task in tasks:  # load_task_set returns sorted by id
        if per_type.get(task.task_type, 0) >= PILOT_PER_TYPE:
            continue
        r = responses.get(task.id)
        if r is None:
            continue
        parse = parse_cot(r["text"])
        if not parse.ok:
            continue
        per_type[task.task_type] = per_type.get(task.task_type, 0) + 1
        selected.append((task, r, parse))
    return selected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-jsonl", default=str(OUT_JSONL))
    ap.add_argument("--out-md", default=str(OUT_MD))
    args = ap.parse_args()

    tasks = {t.id: t for t in load_task_set(TASK_SET)}
    responses = {}
    with open(BASELINE, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["task_id"] in tasks:  # canonical tasks only (skips 041/044)
                responses[r["task_id"]] = r

    pilot = _select_pilot(list(tasks.values()), responses)
    out_jsonl = Path(args.out_jsonl)
    out_md = Path(args.out_md)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    md: list[str] = [
        f"# Corruption pilot sample ({PILOT_SAMPLE_VERSION})",
        "",
        "E3 review gate (spec §7.2). One block per task: each condition shows",
        "the ORIGINAL step, the CORRUPTED step, and (for review) the implied",
        "answer method. Full corrupted CoT is in the JSONL (``corrupted_cot``).",
        "",
    ]

    n_records = 0
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for task, resp, parse in pilot:
            ctx = _task_context(task)
            records = build_records(task.id, "baseline", parse, include_shams=True)
            report = apply_semantic_edits(records, parse, ctx)
            # Mechanical omission/truncation: fill corrupted_cot for review.
            from src.corruption import apply_mechanical

            for rec in records:
                if (
                    rec.validation_status == "pending"
                    and rec.corruption_type in ("omission", "truncation")
                ):
                    apply_mechanical(rec, parse)
                rec.validate()

            md.append(f"## {task.id} ({task.task_type}) — {len(parse.steps)} steps")
            md.append("")
            md.append(f"- ground truth: `{task.ground_truth}`")
            md.append(f"- baseline answer: `{resp.get('final_answer')}`")
            if report.needs_adjudication:
                md.append(
                    f"- **needs adjudication**: {len(report.needs_adjudication)} "
                    f"conditions (see JSONL)"
                )
            md.append("")
            for rec in records:
                flag = ""
                if rec.validation_status == "na_short_cot":
                    flag = " _(N/A short CoT)_"
                elif rec.corrupted_step is None and rec.corruption_type in (
                    "subtle",
                    "reversal",
                ):
                    flag = " **(UNAPPLIED — adjudicate)**"
                adj = " [position_adjusted]" if rec.position_adjusted else ""
                md.append(
                    f"### {rec.corruption_type}/{rec.position}{adj}{flag} "
                    f"`{rec.corruption_id}`"
                )
                md.append("")
                if rec.original_step is not None:
                    md.append(f"- **orig**: {rec.original_step}")
                if rec.corrupted_step is not None:
                    md.append(f"- **edit**: {rec.corrupted_step}")
                if rec.implied_answer is not None:
                    md.append(
                        f"- **implied**: `{rec.implied_answer}` "
                        f"({rec.implied_answer_method})"
                    )
                elif rec.implied_answer_method == "not_defined":
                    md.append("- **implied**: not_defined")
                md.append("")

            payload = {
                "schema": "corruption_pilot_sample",
                "schema_version": 1,
                "pilot_sample_version": PILOT_SAMPLE_VERSION,
                "task_id": task.id,
                "task_type": task.task_type,
                "n_steps": len(parse.steps),
                "conditions": [r.to_dict() for r in records],
                "needs_adjudication": report.needs_adjudication,
            }
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            n_records += len(records)

    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    # Frozen sample fingerprint for the review record.
    digest = hashlib.sha256(out_jsonl.read_bytes()).hexdigest()
    summary = {
        "pilot_sample_version": PILOT_SAMPLE_VERSION,
        "n_tasks": len(pilot),
        "n_conditions": n_records,
        "jsonl_sha256": digest,
        "output": str(out_jsonl.relative_to(REPO)),
        "review_packet": str(out_md.relative_to(REPO)),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
