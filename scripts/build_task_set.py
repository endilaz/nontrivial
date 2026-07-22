"""Build Phase 1 task-set artifacts (Milestones B2–B7).

Usage:
    .venv/bin/python scripts/build_task_set.py build     # candidates + review form
    .venv/bin/python scripts/build_task_set.py merge     # canonical task set + manifest
    .venv/bin/python scripts/build_task_set.py schema    # export task_set.schema.json
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.task_review import export_review_form, merge_canonical
from src.task_set import DEFAULT_CONFIG_PATH, build_candidates

CANDIDATES = REPO_ROOT / "data" / "tasks" / "review" / "candidates.jsonl"
DECISIONS = REPO_ROOT / "data" / "tasks" / "review" / "review_decisions.jsonl"
FORM = REPO_ROOT / "data" / "tasks" / "review" / "review_form.csv"
TASK_SET = REPO_ROOT / "data" / "tasks" / "task_set.jsonl"
MANIFEST = REPO_ROOT / "data" / "tasks" / "task_set_manifest.json"


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "build"
    if command == "build":
        records = build_candidates(config_path=DEFAULT_CONFIG_PATH, output_path=CANDIDATES)
        count = export_review_form(CANDIDATES, FORM)
        print(f"wrote {len(records)} candidates -> {CANDIDATES}")
        print(f"wrote review form ({count} rows) -> {FORM}")
        return 0
    if command == "merge":
        merge_canonical(CANDIDATES, DECISIONS, TASK_SET, MANIFEST, config_path=DEFAULT_CONFIG_PATH)
        print(f"wrote canonical task set -> {TASK_SET}")
        print(f"wrote manifest -> {MANIFEST}")
        return 0
    if command == "schema":
        from scripts.export_task_schema import main as export_main

        export_main()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
