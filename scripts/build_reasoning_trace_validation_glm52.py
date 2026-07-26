"""Build the GLM reasoning-trace judge validation sample (2026-07-26).

The reasoning+content judging (reveal_judged_reasoning.json) returned 97-100%
explicit — the strongest claim in the project. Before it goes in any writeup,
the researcher spot-checks that the judge is not hallucinating disclosure in
native reasoning traces. Composition: 15 judged-explicit + 5 judged-none (all 2
none rows + 3 unscored/near-boundary if available), reasoning trace + content
shown exactly as the judge saw it (reasoning first, then content).

Blind by construction: no judge labels in the output rows. Deterministic seed.
"""

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE = REPO_ROOT / "data" / "results" / "judge_runs" / "admission_hint_paradigm_f_glm_5_2_v1_full_reasoning.jsonl"
RESP = REPO_ROOT / "data" / "results" / "hint_paradigm_f_glm_5_2_v1_full" / "responses.jsonl"
OUT = REPO_ROOT / "data" / "validation" / "reasoning_trace_validation_glm52_v1.jsonl"
SEED = 20260726
N_EXPLICIT = 15


def reasoning_plus_content(record, run_dir: Path) -> str:
    raw_path = record.get("raw_response_path")
    reasoning = ""
    if raw_path:
        raw = json.loads((REPO_ROOT / raw_path).read_text(encoding="utf-8"))
        msg = (raw.get("choices") or [{}])[0].get("message") or {}
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return f"{reasoning}\n\n{record.get('text') or ''}".strip()


def main() -> None:
    random.seed(SEED)
    run_dir = RESP.parent
    judgments = {}
    for line in JUDGE.open():
        j = json.loads(line)
        judgments[(j["task_id"], j["condition_id"])] = (j.get("judge_reply") or {}).get(
            "disclosure", j.get("decision")
        )

    records = {}
    for line in RESP.open():
        r = json.loads(line)
        records[(r["task_id"], r["condition_id"])] = r

    explicit = sorted(k for k, v in judgments.items() if v == "explicit")
    non_explicit = sorted(k for k, v in judgments.items() if v != "explicit")
    sel = random.sample(explicit, N_EXPLICIT) + non_explicit  # all non-explicit (few)
    random.shuffle(sel)

    with OUT.open("w") as f:
        for i, (tid, cond) in enumerate(sel):
            r = records[(tid, cond)]
            row = {
                "schema": "reasoning_trace_validation_row",
                "schema_version": 1,
                "row_id": f"glm52rt-{i + 1:03d}",
                "task_id": tid,
                "condition_id": cond,
                "cue_target": (r.get("cue") or {}).get("target"),
                "reasoning_and_content": reasoning_plus_content(r, run_dir),
                "researcher_label": None,  # fill: explicit | indirect | none
                "dev_or_holdout": "dev" if i % 5 < 3 else "holdout",
            }
            f.write(json.dumps(row) + "\n")

    print(f"wrote {len(sel)} rows -> {OUT.relative_to(REPO_ROOT)}")
    print(f"composition: explicit {N_EXPLICIT}/{len(explicit)} | non-explicit {len(non_explicit)} (all)")


if __name__ == "__main__":
    main()
