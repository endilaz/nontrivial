"""Export ``data/tasks/task_set.schema.json`` from the Pydantic task schema.

The JSON Schema file is a generated artifact of ``src/task_schema.py``; never
edit it by hand. A test asserts the committed file is in sync.

Usage: .venv/bin/python scripts/export_task_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.task_schema import TaskSetEnvelope

OUTPUT_PATH = REPO_ROOT / "data" / "tasks" / "task_set.schema.json"


def main() -> None:
    schema = TaskSetEnvelope.model_json_schema()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
