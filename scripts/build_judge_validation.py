"""Build the C3 answer-equivalence judge validation set (plan.md §C3).

Samples >=50 answer pairs from real baseline outputs, stratified by task type
and match difficulty (researcher decision 2026-07-22, option A: stratified,
oversampling ambiguous/unscored cases). The set is FROZEN before any judge
prompt tuning: this script writes the pairs plus a blind labeling form, and
records the frozen file's SHA-256 in a manifest. Researcher labels are filled
in WITHOUT seeing any judge decision (no judge has run at that point by
construction).

Difficulty strata per task type (cells that don't exist are skipped):

* ``unscored``/ambiguous  — taken exhaustively (these are the judge's raison
  d'etre: alias candidates, conflicting forms).
* ``incorrect``           — taken exhaustively (clearly-wrong cases; math
  re-scored with the post-2026-07-22 currency-aware parser).
* ``correct``             — uniform random fill (exact/near-exact matches).

Sampling is deterministic given ``--seed``; the seed and per-stratum counts
are recorded in the manifest. Ground truth is included so the researcher can
label equivalence; the researcher adds ``researcher_label`` (equivalent |
not_equivalent | ambiguous) per pair. A held-out split (``holdout`` flag) is
marked deterministically: if any judge prompt tuning happens, tuning uses the
dev portion and the held-out portion stays untouched for the final evaluation.

No model calls are made here. Offline and deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation import evaluate_answer  # noqa: E402

DEFAULT_RESPONSES = REPO_ROOT / "data" / "results" / "baseline_deepseek_v4_flash_v2_full" / "responses.jsonl"
OUT_DIR = REPO_ROOT / "data" / "validation"
SET_NAME = "judge_validation_v1"
HOLDOUT_FRACTION = 0.4  # 40% held out from any tuning; final evaluation only
MIN_PAIRS = 50


def difficulty_stratum(record: Dict[str, Any]) -> str:
    """Map a baseline record to its difficulty stratum using the CURRENT
    deterministic evaluator (post currency fix), not the stored category."""
    category = record["category"]
    if category == "unscored":
        return "ambiguous"
    evaluation = evaluate_answer(
        record["final_answer"], record["ground_truth"], record["answer_format"]
    )
    if evaluation.decision == "equivalent":
        return "exact_match"
    if evaluation.decision == "unscored":
        return "ambiguous"
    return "clearly_wrong"


def build_pairs(
    records: Sequence[Dict[str, Any]],
    *,
    seed: int,
    holdout_fraction: float = HOLDOUT_FRACTION,
) -> List[Dict[str, Any]]:
    """Stratified sample: all ambiguous + all clearly_wrong per type, exact
    matches randomly to fill, deterministic under ``seed``."""
    rng = random.Random(seed)
    by_type_stratum: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for record in records:
        if record["category"] in ("generation_failure", "truncation", "parse_failure"):
            continue  # nothing to judge: no usable final answer
        stratum = difficulty_stratum(record)
        by_type_stratum.setdefault(record["task_type"], {}).setdefault(stratum, []).append(record)

    pairs: List[Dict[str, Any]] = []
    for task_type in sorted(by_type_stratum):
        strata = by_type_stratum[task_type]
        taken: List[Dict[str, Any]] = []
        # Ambiguous + clearly wrong: exhaustive (oversampling per decision A).
        taken.extend(strata.get("ambiguous", []))
        taken.extend(strata.get("clearly_wrong", []))
        # Exact matches: random fill to >= MIN_PAIRS/4 per type where possible.
        exact = list(strata.get("exact_match", []))
        rng.shuffle(exact)
        target = max(MIN_PAIRS // 4 - len(taken), min(3, len(exact)))
        taken.extend(exact[:target])
        for record in taken:
            pairs.append(
                {
                    "pair_id": f"{SET_NAME}-{len(pairs)+1:03d}",
                    "task_id": record["task_id"],
                    "task_type": record["task_type"],
                    "stratum": difficulty_stratum(record),
                    "answer_format": record["answer_format"],
                    "ground_truth": record["ground_truth"],
                    "model_answer": record["final_answer"],
                    "deterministic_decision": evaluate_answer(
                        record["final_answer"], record["ground_truth"], record["answer_format"]
                    ).decision,
                    "researcher_label": None,  # filled by the researcher, blind
                    "researcher_notes": None,
                    "holdout": False,  # assigned below
                }
            )

    # Deterministic holdout assignment: shuffle (seeded), then round-robin so
    # every task type and stratum is represented in both splits.
    rng.shuffle(pairs)
    stride = max(round(1 / holdout_fraction), 2)
    for i, pair in enumerate(pairs):
        pair["holdout"] = i % stride == 0
    # Stable sequential pair_ids sorted by type/id for easy reference.
    for i, pair in enumerate(sorted(pairs, key=lambda p: (p["task_type"], p["task_id"]))):
        pair["pair_id"] = f"{SET_NAME}-{i+1:03d}"
    return sorted(pairs, key=lambda p: p["pair_id"])


def freeze_set(pairs: List[Dict[str, Any]], out_dir: Path, *, seed: int, source: Path) -> Dict[str, Path]:
    """Write the validation set, a blind labeling form, and a manifest with
    the frozen SHA-256. Refuses to overwrite (frozen means frozen)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    set_path = out_dir / f"{SET_NAME}.jsonl"
    form_path = out_dir / f"{SET_NAME}_labeling_form.md"
    manifest_path = out_dir / f"{SET_NAME}_manifest.json"
    for path in (set_path, form_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"frozen validation artifact already exists: {path}")

    with set_path.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
    set_sha = hashlib.sha256(set_path.read_bytes()).hexdigest()

    lines = [
        f"# Judge validation labeling form ({SET_NAME})",
        "",
        "Label each pair: is the model answer equivalent to the ground truth?",
        "Fill `researcher_label` in the JSONL with one of: `equivalent`,",
        "`not_equivalent`, `ambiguous`. Judge decisions are NOT shown — there",
        "are none yet; label blind (plan.md §C3).",
        "",
        "| pair_id | task_type | stratum | ground_truth | model_answer |",
        "|---|---|---|---|---|",
    ]
    for pair in pairs:
        lines.append(
            f"| {pair['pair_id']} | {pair['task_type']} | {pair['stratum']} "
            f"| {pair['ground_truth']} | {pair['model_answer']} |"
        )
    form_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = {
        "schema": "judge_validation_set",
        "schema_version": 1,
        "name": SET_NAME,
        "created_on": date.today().isoformat(),
        "source_responses": str(source.relative_to(REPO_ROOT)) if source.is_relative_to(REPO_ROOT) else str(source),
        "seed": seed,
        "n_pairs": len(pairs),
        "strata_counts": _strata_counts(pairs),
        "holdout_fraction": HOLDOUT_FRACTION,
        "n_holdout": sum(1 for p in pairs if p["holdout"]),
        "validation_set_sha256": set_sha,
        "frozen": True,
        "notes": (
            "Frozen before any judge prompt tuning (plan.md §C3). If tuning "
            "occurs, use only pairs with holdout=false; evaluate on holdout=true."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"set": set_path, "form": form_path, "manifest": manifest_path}


def _strata_counts(pairs: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for pair in pairs:
        key = f"{pair['task_type']}:{pair['stratum']}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--responses", default=str(DEFAULT_RESPONSES))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--seed", type=int, default=20260722)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source = Path(args.responses)
    records = [json.loads(l) for l in source.open(encoding="utf-8") if l.strip()]
    pairs = build_pairs(records, seed=args.seed)
    if len(pairs) < MIN_PAIRS:
        raise SystemExit(f"only {len(pairs)} pairs (< {MIN_PAIRS}); refusing to freeze")
    paths = freeze_set(pairs, Path(args.out_dir), seed=args.seed, source=source)
    print(json.dumps(_strata_counts(pairs), indent=2))
    print(f"pairs: {len(pairs)} (holdout: {sum(1 for p in pairs if p['holdout'])})")
    for name, path in paths.items():
        print(f"{name}: {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
