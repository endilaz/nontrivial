"""Build the GLM-5.2 admission-judge RE-validation sample (2026-07-26).

Mirrors build_admission_revalidation_gpt54.py: the F3 judge (v1.1.1) must be
re-validated per model before GLM judged reveal rates are canonical. GLM's
judged-visible-answer profile differs sharply (57-60% explicit on buried cues),
so the sample checks both classes: all judged-`explicit` are too many (76) —
take a stratified random 20 across conditions — plus judged-`none` rows
oversampling grader (the flipped-headline class for GLM is none -> missed
disclosure on buried cues).

Blind by construction: rows carry only what the blinded judge saw (CoT text +
cue target). Deterministic seed.
"""

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE = REPO_ROOT / "data" / "results" / "judge_runs" / "admission_hint_paradigm_f_glm_5_2_v1_full.jsonl"
RESP = REPO_ROOT / "data" / "results" / "hint_paradigm_f_glm_5_2_v1_full" / "responses.jsonl"
OUT = REPO_ROOT / "data" / "validation" / "admission_revalidation_glm52_v1.jsonl"
SEED = 20260726
N_EXPLICIT = 20
N_NONE = 24


def main() -> None:
    random.seed(SEED)
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
    none = sorted(k for k, v in judgments.items() if v == "none")
    sel = random.sample(explicit, N_EXPLICIT) + random.sample(none, N_NONE)
    random.shuffle(sel)

    with OUT.open("w") as f:
        for i, (tid, cond) in enumerate(sel):
            r = records[(tid, cond)]
            row = {
                "schema": "admission_revalidation_row",
                "schema_version": 1,
                "row_id": f"glm52-{i + 1:03d}",
                "task_id": tid,
                "condition_id": cond,
                "cue_target": (r.get("cue") or {}).get("target"),
                "cot_text": r.get("text"),
                "researcher_label": None,  # fill: explicit | indirect | none
                "dev_or_holdout": "dev" if i % 5 < 3 else "holdout",
            }
            f.write(json.dumps(row) + "\n")

    print(f"wrote {len(sel)} rows -> {OUT.relative_to(REPO_ROOT)}")
    print(f"composition: explicit {N_EXPLICIT}/{len(explicit)} | none {N_NONE}/{len(none)}")


if __name__ == "__main__":
    main()
