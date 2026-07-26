"""Build the GPT-5.4 admission-judge RE-validation sample (2026-07-25).

The F3 judge (rubric v1.1.1) was validated on DeepSeek transcripts only. Before
GPT-5.4 judged reveal rates are canonical, a researcher-labeled sample checks
the judge on this model's terser style. Composition targets the decision that
matters: all judged-`explicit` rows (are the positives real?) plus random
judged-`none` authority rows (the flipped-headline class) and a few judged-none
rows from other conditions.

Blind by construction: rows carry only what the blinded judge saw (CoT text +
cue target). No judge labels are included. Deterministic seed.
"""

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE = REPO_ROOT / "data" / "results" / "judge_runs" / "admission_hint_paradigm_f_gpt_5_4_v1_full.jsonl"
RESP = REPO_ROOT / "data" / "results" / "hint_paradigm_f_gpt_5_4_v1_full" / "responses.jsonl"
OUT = REPO_ROOT / "data" / "validation" / "admission_revalidation_gpt54_v1.jsonl"
SEED = 20260725
N_NONE_AUTHORITY = 20
N_NONE_OTHER = 11


def main() -> None:
    random.seed(SEED)
    judgments = {}
    for line in JUDGE.open():
        j = json.loads(line)
        # Judgment schema: the label lives in judge_reply.disclosure (decision mirrors it).
        judgments[(j["task_id"], j["condition_id"])] = (j.get("judge_reply") or {}).get(
            "disclosure", j.get("decision")
        )

    records = {}
    for line in RESP.open():
        r = json.loads(line)
        records[(r["task_id"], r["condition_id"])] = r

    explicit = sorted(k for k, v in judgments.items() if v == "explicit")
    none_auth = sorted(k for k, v in judgments.items() if v == "none" and k[1] == "authority_cue")
    none_other = sorted(k for k, v in judgments.items() if v == "none" and k[1] != "authority_cue")

    sel = explicit + random.sample(none_auth, N_NONE_AUTHORITY) + random.sample(none_other, N_NONE_OTHER)
    random.shuffle(sel)

    with OUT.open("w") as f:
        for i, (tid, cond) in enumerate(sel):
            r = records[(tid, cond)]
            row = {
                "schema": "admission_revalidation_row",
                "schema_version": 1,
                "row_id": f"gpt54-{i + 1:03d}",
                "task_id": tid,
                "condition_id": cond,
                "cue_target": (r.get("cue") or {}).get("target"),
                "cot_text": r.get("text"),
                "researcher_label": None,  # fill: explicit | indirect | none
                "dev_or_holdout": "dev" if i % 5 < 3 else "holdout",
            }
            f.write(json.dumps(row) + "\n")

    print(f"wrote {len(sel)} rows -> {OUT.relative_to(REPO_ROOT)}")
    print(f"composition: explicit {len(explicit)} | none-authority {N_NONE_AUTHORITY} | none-other {N_NONE_OTHER}")


if __name__ == "__main__":
    main()
