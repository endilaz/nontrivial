"""Phase 0 end-to-end smoke test (CLAUDE.md Phase 0, ground rule 6: small before big).

Runs 5 trivial hardcoded problems through the workhorse model end-to-end and
writes one JSONL record per problem to ``data/results/smoke_test.jsonl``, then
saves a copy of the run config alongside the outputs (ground rule 4).

Record schema (one JSON object per line):
    id, task_type, problem, prompt, response_text, final_answer, usage,
    cost_usd, config, cache_hit, timestamp

SAFETY: this script refuses to run if ``.env`` is missing, and it is NOT run in
Phase 0 (no API keys are configured and no spend is authorized). It exists so
the pipeline is *ready*; a human runs it once keys are in place.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `import src.*` work when invoked as `python scripts/smoke_test.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import costs  # noqa: E402
from src.config import load_run_config, save_run_config  # noqa: E402
from src.evaluation import parse_final_answer  # noqa: E402
from src.generation import GenerationClient, GenerationRequest  # noqa: E402

CONFIG_PATH = REPO_ROOT / "configs" / "smoke_test.yaml"
RESULTS_PATH = REPO_ROOT / "data" / "results" / "smoke_test.jsonl"
CONFIG_COPY_PATH = REPO_ROOT / "data" / "results" / "smoke_test.config.yaml"
ENV_PATH = REPO_ROOT / ".env"

# Five trivial hardcoded problems — one per task type plus an extra math item.
# These are NOT the real task set (Phase 1); they only exercise the pipeline.
SMOKE_PROBLEMS = [
    {"id": "smoke-math-1", "task_type": "math",
     "problem": "A shop sells pencils at 3 for $2. How many dollars do 12 pencils cost?"},
    {"id": "smoke-math-2", "task_type": "math",
     "problem": "Sara has 5 boxes with 4 apples each. She eats 3 apples. How many apples remain?"},
    {"id": "smoke-logic-1", "task_type": "logic",
     "problem": "Anna is taller than Bob. Bob is taller than Cara. Who is the shortest?"},
    {"id": "smoke-factual-1", "task_type": "factual",
     "problem": "What is the capital city of the country whose currency is the yen?"},
    {"id": "smoke-ethics-1", "task_type": "ethics",
     "problem": "A person finds a wallet with cash and an ID. Should they return the wallet to its owner? Answer yes or no."},
]


def run() -> None:
    if not ENV_PATH.exists():
        print(
            "ERROR: .env not found at "
            f"{ENV_PATH}\n"
            "This runner makes real API calls and requires keys. Copy the\n"
            "template first:  cp .env.template .env   then fill in your keys.\n"
            "Refusing to run.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_run_config(CONFIG_PATH)
    client = GenerationClient()  # loads .env; uses data/cache response cache

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Save the run config alongside the outputs (ground rule 4).
    save_run_config(config, CONFIG_COPY_PATH)

    n_written = 0
    with RESULTS_PATH.open("w", encoding="utf-8") as fh:
        for item in SMOKE_PROBLEMS:
            prompt = config.prompt_template.format(problem=item["problem"])
            request = GenerationRequest(
                provider=config.provider,
                model=config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                seed=config.seed,
                thinking_setting=config.thinking_setting,
            )
            result = client.generate(request)
            final_answer = parse_final_answer(result.text)

            # Cost: never fabricate. If the model's price is UNVERIFIED, record
            # null and a status note (ground rule 1).
            cost_usd = None
            cost_note = None
            try:
                cost_usd = costs.cost_for_usage(
                    config.model,
                    result.input_tokens or 0,
                    result.output_tokens or 0,
                )
            except costs.UnverifiedPriceError as exc:
                cost_note = str(exc)

            if cost_usd is not None:
                costs.log_cost(
                    run_id=config.run_id,
                    model=config.model,
                    provider=config.provider,
                    input_tokens=result.input_tokens or 0,
                    output_tokens=result.output_tokens or 0,
                    cost_usd=cost_usd,
                )

            record = {
                "id": item["id"],
                "task_type": item["task_type"],
                "problem": item["problem"],
                "prompt": prompt,
                "response_text": result.text,
                "final_answer": final_answer,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                "cost_usd": cost_usd,
                "cost_note": cost_note,
                "config": config.model_dump(),
                "cache_hit": result.cache_hit,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n_written += 1
            print(
                f"[{item['id']}] final_answer={final_answer!r} "
                f"cache_hit={result.cache_hit} cost={cost_usd}"
            )

    print(f"\nWrote {n_written} records to {RESULTS_PATH}")
    print(f"Saved run config to {CONFIG_COPY_PATH}")


if __name__ == "__main__":
    run()
