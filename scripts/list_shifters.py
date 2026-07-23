"""List the C4 shifter tasks (cue-condition answer matched the cue target).

Reads the canonical summary's per-type outcomes and prints the task-level
records for inspection. Deterministic shifters come from responses.jsonl;
the judge-resolved factual shifter is excluded here because its judge
decision was not_equivalent (see judgments.jsonl)."""
import json

RUN = "data/results/replication_chen2025_suggested_cue_full_v2"
recs = [json.loads(l) for l in open(f"{RUN}/responses.jsonl")]

for i in range(len(recs)):
    r = recs[i]
    if r["condition_id"] != "suggested_cue":
        continue
    if (r.get("cue_evaluation") or {}).get("decision") == "equivalent" and (r.get("cue") or {}).get("ok"):
        print(
            f"{r['task_id']:11s} | cue: {r['cue']['target']:8s} | "
            f"answer: {r['final_answer']:8s} | revealed: {(r.get('reveal') or {}).get('revealed')} | ground truth: {r['ground_truth']:8s}"
        )
        print("REASONING WITHOUT CUE: ")
        print(recs[i+1]['text'])
        print("-" * 80)
        print("REASONING WITH CUE: ")
        print(r['text'])
        print("-" * 80)
        input()