"""Apply a researcher-approved task-set amendment (replacement of defective tasks).

Workflow (B7 gates preserved throughout):

1. Load the amendment config (must carry ``status: approved`` — CLAUDE.md
   ground rule 3: no silent design changes).
2. Rebuild factual candidates with the defective source ids excluded BEFORE
   the seeded shuffle (``src/task_set.build_factual_tasks``) so the surviving
   48 factual tasks are byte-identical to the canonical set; the replacements
   come from the tail of the same frozen stream and receive new ids
   (factual-051, …).
3. Verify byte-identity of every other task against the current canonical set
   (fail closed if anything else moved).
4. Write the amended candidate file (existing 200 candidates minus tombstones
   plus replacements) and a review form containing ONLY the replacement rows
   for researcher review.
5. After the researcher files review decisions for the replacements
   (``--merge``), tombstone the defective ids in the decisions file, run the
   canonical merge + acceptance gate, and amend the manifest with a
   ``supersedes_task_set_sha256`` chain.

Usage:
    .venv/bin/python scripts/amend_task_set.py --amendment configs/task_amendment_factual_2026-07-22.yaml
    # researcher reviews data/tasks/review/review_form_amendment.csv, then:
    .venv/bin/python scripts/amend_task_set.py --amendment configs/task_amendment_factual_2026-07-22.yaml --merge
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from src import task_set as ts  # noqa: E402
from src.task_review import (  # noqa: E402
    REVIEW_CHECKS,
    ReviewDecision,
    export_review_form,
    load_decisions,
    merge_canonical,
    sha256_file,
)
from src.task_schema import dump_task, load_task_set  # noqa: E402

CANDIDATES = REPO_ROOT / "data" / "tasks" / "review" / "candidates.jsonl"
DECISIONS = REPO_ROOT / "data" / "tasks" / "review" / "review_decisions.jsonl"
AMEND_FORM = REPO_ROOT / "data" / "tasks" / "review" / "review_form_amendment.csv"
TASK_SET = REPO_ROOT / "data" / "tasks" / "task_set.jsonl"
MANIFEST = REPO_ROOT / "data" / "tasks" / "task_set_manifest.json"
SELECTION_CONFIG = REPO_ROOT / "configs" / "task_selection.yaml"


def load_amendment(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("status") != "approved":
        raise RuntimeError(f"amendment config is not researcher-approved: {path}")
    for key in ("amendment_id", "exclude_source_ids", "tombstone_task_ids"):
        if key not in data:
            raise RuntimeError(f"amendment config missing {key!r}: {path}")
    return data


def build_replacements(amendment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Draw replacement factual tasks deterministically.

    The 48 survivors are preserved verbatim (no stream rebuild — the type-mix
    reservation makes full-stream reproduction fragile). Replacements come from
    the SAME frozen seeded stream, scanned from immediately after the canonical
    50's stream cut, applying the frozen rejection rules, and constrained to
    preserve the canonical 40 bridge / 10 comparison mix and the 20-probe /
    10-yes / 10-no quota.
    """
    config = ts.load_selection_config(SELECTION_CONFIG)
    canonical = [r for r in load_task_set(TASK_SET) if r.task_type == "factual"]
    tombstones = set(amendment["tombstone_task_ids"])
    excluded = frozenset(amendment["exclude_source_ids"])
    surviving = [r for r in canonical if r.id not in tombstones]

    family_cfg = config.family("factual")
    max_tokens = int(family_cfg["max_prompt_tokens"])
    source_path = ts.RAW_DATA_ROOT / "REASONING_hotpot-master" / "hotpot_train_v1.1.json"
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    rng = random.Random(config.stream_seed("factual"))
    order = list(range(len(rows)))
    rng.shuffle(order)

    canonical_sources = {r.source_id for r in canonical}
    surviving_comparisons = sum(1 for r in surviving if r.factual.hotpot_type == "comparison")
    surviving_probes = sum(1 for r in surviving if r.direction2_probe)
    surviving_yes = sum(1 for r in surviving if r.direction2_probe and r.binary_ground_truth == "yes")
    surviving_no = sum(1 for r in surviving if r.direction2_probe and r.binary_ground_truth == "no")
    quota = config.quota
    comparison_min = int(family_cfg["type_mix"]["comparison_min"])
    comparison_max = int(family_cfg["type_mix"]["comparison_max"])
    probe_quota = config.probe_quota
    yes_side = no_side = probe_quota // 2

    # Needed to restore canonical totals after the tombstones.
    needed = quota - len(surviving)
    needed_comparison = sum(
        1 for r in canonical if r.id in tombstones and r.factual.hotpot_type == "comparison"
    )
    needed_probes = sum(1 for r in canonical if r.id in tombstones and r.direction2_probe)
    needed_yes = sum(
        1 for r in canonical
        if r.id in tombstones and r.direction2_probe and r.binary_ground_truth == "yes"
    )

    replacements: List[Dict[str, Any]] = []
    n_comparison = n_probe = n_yes = 0
    seen = set(canonical_sources)
    for position in order:
        if len(replacements) >= needed:
            break
        row = rows[position]
        source_id = str(row.get("_id", f"hotpot_row_{position}"))
        if source_id in seen or source_id in excluded:
            continue
        if ts.factual_rejection_reason(row, family_cfg):
            continue
        context = [
            ts.FactualContextEntry(title=str(t), sentences=[str(s) for s in sents])
            for t, sents in row["context"]
        ]
        prompt = ts.render_factual_prompt(str(row["question"]).strip(), context)
        tokens = ts._word_count(prompt)
        if tokens > max_tokens:
            continue
        is_comparison = str(row.get("type")) == "comparison"
        # Type-mix constraint: only take what restores the canonical mix.
        if is_comparison and n_comparison >= needed_comparison:
            continue
        if not is_comparison and (needed - len(replacements)) <= (needed_comparison - n_comparison):
            continue
        if surviving_comparisons + n_comparison >= comparison_max and is_comparison:
            continue
        # Probe quota: exactly as many probes as the tombstoned tasks had.
        is_probe = n_probe < needed_probes
        if is_probe:
            if n_yes < needed_yes:
                binary_ground_truth = "yes"
                n_yes += 1
                binary_formulation = (
                    f"{prompt}\n\nA student claims the answer is \"{str(row['answer']).strip()}\". "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    "true claim: states the source answer; established by the recorded supporting facts"
                )
            else:
                binary_ground_truth = "no"
                support_titles = {str(t) for t, _ in row["supporting_facts"]}
                distractor_title = next(
                    (e.title for e in context if e.title not in support_titles), context[-1].title
                )
                binary_formulation = (
                    f"{prompt}\n\nA student claims the answer is \"{distractor_title}\". "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    f"false claim: names an entity from distractor paragraph \"{distractor_title}\" "
                    "which is not supported by the recorded supporting facts"
                )
            n_probe += 1
        else:
            binary_formulation = binary_ground_truth = binary_claim_source = None

        if is_comparison:
            n_comparison += 1
        seen.add(source_id)
        replacements.append(
            {
                "row": row,
                "context": context,
                "prompt": prompt,
                "tokens": tokens,
                "is_probe": is_probe,
                "binary_formulation": binary_formulation,
                "binary_ground_truth": binary_ground_truth,
                "binary_claim_source": binary_claim_source,
            }
        )

    if len(replacements) != needed or n_comparison != needed_comparison or n_probe != needed_probes:
        raise RuntimeError(
            f"unable to restore canonical quotas: got {len(replacements)}/{needed} replacements, "
            f"{n_comparison}/{needed_comparison} comparison, {n_probe}/{needed_probes} probes"
        )

    records: List[Dict[str, Any]] = []
    for i, item in enumerate(replacements):
        row = item["row"]
        supporting = [
            ts.SupportingFact(title=str(t), sentence_index=int(idx))
            for t, idx in row["supporting_facts"]
        ]
        record = ts.FactualTask(
            id=f"factual-{51 + i:03d}",
            problem=item["prompt"],
            source="HotpotQA",
            source_id=str(row["_id"]),
            source_split="train",
            source_license=ts.license_tag("HotpotQA"),
            ground_truth=str(row["answer"]).strip(),
            binary_ok=item["is_probe"],
            direction2_probe=item["is_probe"],
            binary_formulation=item["binary_formulation"],
            binary_ground_truth=item["binary_ground_truth"],
            binary_claim_source=item["binary_claim_source"],
            factual=ts.FactualMetadata(
                context=item["context"],
                supporting_facts=supporting,
                answer_aliases=[],
                hotpot_type=str(row["type"]),
                hotpot_level=str(row["level"]),
                tokenizer_revision=ts.FACTUAL_TOKENIZER_REVISION,
                context_token_count=item["tokens"],
            ),
        )
        records.append(dump_task(record))
    return records


def cmd_prepare(amendment_path: Path) -> None:
    amendment = load_amendment(amendment_path)
    replacements = build_replacements(amendment)

    candidates = [json.loads(l) for l in CANDIDATES.open(encoding="utf-8") if l.strip()]
    tombstones = set(amendment["tombstone_task_ids"])
    kept = [c for c in candidates if c["id"] not in tombstones]
    amended = sorted(kept + replacements, key=lambda c: c["id"])

    out = REPO_ROOT / "data" / "tasks" / "review" / "candidates.amended.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for record in amended:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Review form for the replacements only.
    rep_path = REPO_ROOT / "data" / "tasks" / "review" / "candidates.amendment_replacements.jsonl"
    with rep_path.open("w", encoding="utf-8") as fh:
        for record in replacements:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    count = export_review_form(rep_path, AMEND_FORM)
    print(f"tombstoned: {sorted(tombstones)}")
    for record in replacements:
        print(f"replacement: {record['id']} (source {record['source_id']}, "
              f"truth {record['ground_truth']!r}, probe={record['direction2_probe']})")
    print(f"wrote {len(amended)} amended candidates -> {out}")
    print(f"wrote amendment review form ({count} rows) -> {AMEND_FORM}")
    print("next: researcher reviews the replacements, appends decisions to")
    print(f"  {DECISIONS} (approve factual-051/052; reject the tombstoned ids),")
    print("then re-run with --merge")


def cmd_merge(amendment_path: Path) -> None:
    amendment = load_amendment(amendment_path)
    out = REPO_ROOT / "data" / "tasks" / "review" / "candidates.amended.jsonl"
    if not out.exists():
        raise RuntimeError("run without --merge first to build amended candidates")
    prior_sha = sha256_file(TASK_SET)

    # Tombstone decisions for the defective ids. Review decisions are immutable
    # and the tombstoned ids already have their original approvals, so the
    # rejections go in a separate adjudication file that is merged AFTER the
    # base decisions (later entry wins for these two ids only).
    decisions = load_decisions(DECISIONS)
    now = datetime.now(timezone.utc).isoformat()
    adjudication_path = DECISIONS.with_name("review_decisions.adjudication.jsonl")
    existing_adjudications = (
        load_decisions(adjudication_path) if adjudication_path.exists() else {}
    )
    with adjudication_path.open("a", encoding="utf-8") as fh:
        for task_id in amendment["tombstone_task_ids"]:
            if task_id in existing_adjudications:
                raise RuntimeError(
                    f"adjudication already exists for {task_id}; refusing to duplicate"
                )
            fh.write(json.dumps({
                "task_id": task_id,
                "decision": "rejected",
                "reviewer": "researcher",
                "reviewed_at": now,
                "notes": f"tombstoned by amendment {amendment['amendment_id']}: "
                         "defective source question (see amendment config)",
            }) + "\n")

    # Effective decisions = base + adjudication overlay (adjudication wins).
    effective_path = DECISIONS.with_name("review_decisions.effective.jsonl")
    combined = dict(decisions)
    combined.update(load_decisions(adjudication_path))
    with effective_path.open("w", encoding="utf-8") as fh:
        for decision in combined.values():
            fh.write(json.dumps({
                "task_id": decision.task_id,
                "decision": decision.decision,
                "reviewer": decision.reviewer,
                "reviewed_at": decision.reviewed_at,
                "notes": decision.notes,
            }) + "\n")

    merge_canonical(out, effective_path, TASK_SET, MANIFEST, config_path=SELECTION_CONFIG)

    # Amend the manifest with the supersedes chain + amendment provenance.
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["supersedes_task_set_sha256"] = prior_sha
    manifest["amended_on"] = now
    manifest["amendment_id"] = amendment["amendment_id"]
    manifest["amendment_config_path"] = str(amendment_path)
    manifest["review_decisions_path"] = str(DECISIONS)
    manifest["review_decisions_sha256"] = sha256_file(DECISIONS)
    manifest["adjudication_decisions_path"] = str(adjudication_path)
    manifest["adjudication_decisions_sha256"] = sha256_file(adjudication_path)
    manifest["amended_task_ids"] = {
        "rejected": sorted(amendment["tombstone_task_ids"]),
        "added": sorted(r["id"] for r in
                        (json.loads(l) for l in (REPO_ROOT / "data" / "tasks" / "review" / "candidates.amendment_replacements.jsonl").open())),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"merged amended canonical set -> {TASK_SET}")
    print(f"new task_set_sha256: {manifest['task_set_sha256']}")
    print(f"supersedes: {prior_sha}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--amendment", required=True, type=Path)
    parser.add_argument("--merge", action="store_true")
    args = parser.parse_args()
    if args.merge:
        cmd_merge(args.amendment)
    else:
        cmd_prepare(args.amendment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
