#!/usr/bin/env python3
"""Build the frozen D3 parser-validation sample (32 CoTs, 8 per task type).

Draws a seeded, deterministic sample of baseline responses from the valid
workhorse baseline runs, parses each with ``src/parsing.parse_cot``, and
writes:

- ``data/validation/parser_sample_v1.jsonl`` — frozen sample manifest: one
  record per sampled CoT with the exact response text, source run/key, parser
  output (steps/spans/method), and a SHA-256 of the reasoning text. Freezing
  means this file is append-only: once written it is never regenerated in
  place (``--force`` refuses unless the file is byte-identical).
- ``data/validation/parser_annotations_v1.jsonl`` — annotation template, one
  record per sampled CoT per the guide §8 schema with empty ``boundaries``
  for the researcher to fill in. Also prints each CoT with character offsets
  to make hand labeling tractable.

Offline only: no model calls, no cost. Run:

    .venv/bin/python scripts/build_parser_validation_sample.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.parsing import parse_cot  # noqa: E402

SAMPLE_SEED = 20260723
SAMPLE_PER_TYPE = 8
TASK_TYPES = ("math", "logic", "factual", "ethics")

# Valid workhorse baselines only (never invalidated runs). amend2 supersedes
# the math rows of `full`; non-math rows come from `full`.
BASELINE_RUNS = (
    "baseline_deepseek_v4_flash_v2_amend2",
    "baseline_deepseek_v4_flash_v2_full",
)

SAMPLE_PATH = Path("data/validation/parser_sample_v1.jsonl")
ANNOTATION_PATH = Path("data/validation/parser_annotations_v1.jsonl")
GUIDE_VERSION = "1.0.0"


def _load_pool() -> list[dict]:
    """All valid baseline responses: math from amend2, rest from full."""
    pool: list[dict] = []
    for run_id in BASELINE_RUNS:
        path = PROJECT_ROOT / "data" / "results" / run_id / "responses.jsonl"
        with open(path) as f:
            for line in f:
                row = json.loads(line)
                if row.get("generation_error"):
                    continue
                text = (row.get("text") or "").strip()
                if not text or row.get("final_answer") is None:
                    continue
                row["source_run_id"] = run_id
                pool.append(row)
    # amend2 supersedes full for math; drop full's math rows.
    pool = [
        r
        for r in pool
        if not (
            r["task_type"] == "math"
            and r["source_run_id"] == "baseline_deepseek_v4_flash_v2_full"
        )
    ]
    return pool


def build_sample() -> list[dict]:
    pool = _load_pool()
    rng = random.Random(SAMPLE_SEED)
    sample: list[dict] = []
    for task_type in TASK_TYPES:
        candidates = sorted(
            (r for r in pool if r["task_type"] == task_type),
            key=lambda r: r["task_id"],
        )
        if len(candidates) < SAMPLE_PER_TYPE:
            raise RuntimeError(
                f"only {len(candidates)} candidates for {task_type}"
            )
        chosen = rng.sample(candidates, SAMPLE_PER_TYPE)
        sample.extend(sorted(chosen, key=lambda r: r["task_id"]))
    return sample


def _record_for(row: dict) -> dict:
    result = parse_cot(row["text"])
    reasoning_sha = hashlib.sha256(
        result.reasoning_text.encode()
    ).hexdigest()
    steps = result.steps
    # Long CoTs (some logic runs enumerate whole grids) get a boundary preview
    # only: full step spans stay available via re-running parse_cot on the
    # frozen response_text, and the annotation records total step count.
    preview_limit = 200
    return {
        "schema": "parser_validation_sample",
        "schema_version": "1.0.0",
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "source_run_id": row["source_run_id"],
        "response_key": [
            row["task_id"],
            row["condition_id"],
            row["mechanism"],
            row["replicate_index"],
        ],
        "response_text": row["text"],
        "reasoning_sha256": reasoning_sha,
        "parse": {
            "parser_version": result.parser_version,
            "ok": result.ok,
            "used_llm_fallback": result.used_llm_fallback,
            "validation_errors": result.validation_errors,
            "n_steps": len(steps),
            "steps_truncated": len(steps) > preview_limit,
            "steps": [
                {
                    "index": s.index,
                    "start_char": s.start_char,
                    "end_char": s.end_char,
                    "method": s.method,
                }
                for s in steps[:preview_limit]
            ],
        },
    }


def _annotation_template(rec: dict) -> dict:
    return {
        "schema": "cot_boundary_annotation",
        "schema_version": "1.0.0",
        "task_id": rec["task_id"],
        "task_type": rec["task_type"],
        "source_run_id": rec["source_run_id"],
        "response_key": rec["response_key"],
        "guide_version": GUIDE_VERSION,
        "annotator": None,
        "annotated_at": None,
        "boundaries": [],
        "n_steps": None,
        "unsure_boundaries": [],
        "notes": None,
        "adjudicated": False,
    }


def _write_frozen(path: Path, records: list[dict], force: bool) -> None:
    content = "".join(
        json.dumps(r, sort_keys=True) + "\n" for r in records
    )
    full = PROJECT_ROOT / path
    if full.exists():
        existing = full.read_text()
        if existing == content:
            print(f"frozen file unchanged: {path}")
            return
        if not force:
            raise SystemExit(
                f"{path} exists and differs; refusing to overwrite a frozen "
                "artifact (use a new versioned filename for a new sample)"
            )
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    print(f"wrote {path} ({len(records)} records)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing differing sample file (NOT for frozen use)",
    )
    parser.add_argument(
        "--print-cots",
        action="store_true",
        help="print each sampled CoT with offsets for hand annotation",
    )
    args = parser.parse_args()

    sample = build_sample()
    records = [_record_for(row) for row in sample]
    bad = [r["task_id"] for r in records if not r["parse"]["ok"]]
    if bad:
        raise SystemExit(f"sampled CoTs failed to parse: {bad}")

    _write_frozen(SAMPLE_PATH, records, args.force)
    _write_frozen(
        ANNOTATION_PATH,
        [_annotation_template(r) for r in records],
        args.force,
    )
    print(
        f"sample: {len(records)} CoTs "
        f"({', '.join(f'{t}=8' for t in TASK_TYPES)}), seed {SAMPLE_SEED}, "
        f"built {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )

    if args.print_cots:
        for rec in records:
            print("=" * 72)
            print(f"{rec['task_id']} ({rec['task_type']})")
            from src.parsing import split_reasoning

            reasoning, _ = split_reasoning(rec["response_text"])
            for i, line in enumerate(reasoning.split("\n")):
                print(f"  {line}")
            print(f"  [reasoning length: {len(reasoning)} chars]")


if __name__ == "__main__":
    main()
