"""Score judge-vs-researcher agreement on the GLM reasoning-trace validation."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VAL = REPO_ROOT / "data" / "validation" / "reasoning_trace_validation_glm52_v1.jsonl"
JUDGE = REPO_ROOT / "data" / "results" / "judge_runs" / "admission_hint_paradigm_f_glm_5_2_v1_full_reasoning.jsonl"


def main() -> None:
    judgments = {}
    for line in JUDGE.open():
        j = json.loads(line)
        judgments[(j["task_id"], j["condition_id"])] = (j.get("judge_reply") or {}).get(
            "disclosure", j.get("decision")
        )

    rows = [json.loads(l) for l in VAL.open()]
    labeled = [r for r in rows if r.get("researcher_label")]
    print(f"labeled: {len(labeled)}/{len(rows)}")
    unlabeled = [r["row_id"] for r in rows if not r.get("researcher_label")]
    if unlabeled:
        print("UNLABELED:", unlabeled)

    for split in ("dev", "holdout", "all"):
        subset = [r for r in labeled if split == "all" or r["dev_or_holdout"] == split]
        if not subset:
            continue
        agree = sum(
            1
            for r in subset
            if judgments[(r["task_id"], r["condition_id"])] == r["researcher_label"]
        )
        print(
            f"\n== {split} (n={len(subset)}): agreement {agree}/{len(subset)} "
            f"= {agree / len(subset):.0%}"
        )
        for r in subset:
            jl = judgments[(r["task_id"], r["condition_id"])]
            rl = r["researcher_label"]
            if jl != rl:
                print(
                    f'  DISAGREE {r["row_id"]} {r["task_id"]} {r["condition_id"]}: '
                    f"judge={jl} you={rl}"
                )


if __name__ == "__main__":
    main()
