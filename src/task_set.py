"""Deterministic Phase 1 task-set builder (Milestones B2–B5).

Reads the researcher-approved selection config (``configs/task_selection.yaml``)
and source datasets, and emits validated candidate records conforming to
``src/task_schema.py``. All sampling decisions come from the config; the builder
never chooses thresholds from observed candidate quality (plan.md §B1).

Candidates are written to the review file configured in the selection config
(``data/tasks/review/candidates.jsonl``) with ``review_status: pending``. The
canonical merge (B7 tooling) combines candidates with immutable human review
decisions to produce ``data/tasks/task_set.jsonl``.

Re-running with the same config and source files produces byte-identical
candidate output.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.dataset_licenses import license_tag
from src.task_schema import (
    dump_task,
    EthicsMetadata,
    EthicsTask,
    FactualContextEntry,
    FactualMetadata,
    FactualTask,
    LogicMetadata,
    LogicTask,
    MathMetadata,
    MathTask,
    MathTransformation,
    SupportingFact,
    TaskRecord,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = REPO_ROOT / "data" / "tasks"
RAW_DATA_ROOT = REPO_ROOT / "data" / "raw"
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "task_selection.yaml"

ETHICS_LABEL_MAPPING_VERSION = "ethics-cm-v1"
FACTUAL_TOKENIZER_REVISION = "whitespace-wordcount-proxy-v1"
LOGIC_SOLVER_NAME = "bigbench-target-verifier"
LOGIC_SOLVER_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #


class SelectionConfigError(RuntimeError):
    """The selection config is missing or internally invalid."""


@dataclass(frozen=True)
class SelectionConfig:
    raw: Dict[str, Any]
    path: Path

    @property
    def root_seed(self) -> int:
        return int(self.raw["rng"]["root_seed"])

    def stream_seed(self, family: str) -> int:
        return self.root_seed + int(self.raw["rng"]["stream_offsets"][family])

    @property
    def quota(self) -> int:
        return int(self.raw["quotas"]["per_task_type"])

    @property
    def probe_quota(self) -> int:
        return int(self.raw["quotas"]["binary_probe_subset"]["per_task_type"])

    def family(self, name: str) -> Dict[str, Any]:
        return dict(self.raw[name])

    def source(self, name: str) -> Dict[str, Any]:
        return dict(self.raw["sources"][name])


def load_selection_config(path: Path | str = DEFAULT_CONFIG_PATH) -> SelectionConfig:
    path = Path(path)
    if not path.exists():
        raise SelectionConfigError(f"selection config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SelectionConfigError(f"selection config is not a mapping: {path}")
    for key in ("rng", "quotas", "sources"):
        if key not in raw:
            raise SelectionConfigError(f"selection config missing required section: {key}")
    return SelectionConfig(raw=raw, path=path)


# --------------------------------------------------------------------------- #
# Rejection logging
# --------------------------------------------------------------------------- #


@dataclass
class RejectionLog:
    """Deterministic record of rejected candidates (reason_code, source_id)."""

    entries: List[Tuple[str, str]] = field(default_factory=list)

    def reject(self, reason_code: str, source_id: str) -> None:
        self.entries.append((reason_code, source_id))

    @property
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for code, _ in self.entries:
            out[code] = out.get(code, 0) + 1
        return out


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(text.split())


def _probe_flags(count: int, probe_quota: int) -> List[bool]:
    return [index < probe_quota for index in range(count)]


# --------------------------------------------------------------------------- #
# B2: math (GSM8K, surface perturbation only — numbers frozen)
# --------------------------------------------------------------------------- #

#: Reviewed compatible-object substitutions (plan.md §B2; expanded 2026-07-23,
#: amendment task_amendment_math_perturbation_2026-07-23.yaml, to cover the 8
#: canonical math tasks with no person name).
OBJECT_SUBSTITUTIONS: Dict[str, str] = {
    "apples": "pears",
    "cookies": "brownies",
    "pencils": "markers",
    "books": "notebooks",
    "marbles": "buttons",
    "stickers": "stamps",
    "cupcakes": "muffins",
    "oranges": "plums",
    # 2026-07-23 expansion (nameless tasks + place-name entities). Each swap
    # was audited against its source problem so the replaced term does not
    # reappear in a conflicting form later in the text.
    "A new factory": "A new plant",
    "soaps": "bars of soap",
    "peaches": "nectarines",
    "shoes": "boots",
    "potatoes": "carrots",
    "Londolozi": "KwaZulu",
    "Myrtle": "Laguna",
    "sailors": "deckhands",
    "scholarships": "grants",
    "employees": "workers",
}

#: Reviewed person-name substitutions. Expanded 2026-07-23 (amendment
#: task_amendment_math_perturbation_2026-07-23.yaml) to cover every person name
#: appearing in the 50 canonical math tasks; replacements were chosen to
#: collide with no source name, no object-dictionary key/value, and no other
#: replacement.
NAME_SUBSTITUTIONS: Dict[str, str] = {
    "Natalia": "Mina",
    "Weng": "Rina",
    "Betty": "Nora",
    "James": "Rufus",
    "Mary": "Leah",
    "John": "Noah",
    "Sarah": "Talia",
    "Tom": "Leo",
    "Emma": "Lila",
    "Mark": "Gabe",
    "Anna": "Iris",
    "Sam": "Theo",
    # 2026-07-23 expansion:
    "Luis": "Mateo",
    "Kenny": "Derek",
    "Blake": "Wesley",
    "Deane": "Keller",
    "Vicente": "Rodrigo",
    "Cindy": "Paula",
    "Hazel": "Autumn",
    "Edward": "Marcus",
    "Koby": "Felix",
    "Cherie": "Bianca",
    "Granger": "Puckett",
    "Jake": "Evan",
    "Jack": "Cole",
    "Minnie": "Pearl",
    "Hannah": "Sierra",
    "Voldemort": "Zoltan",
    "Annie": "Clara",
    "Carl": "Hugo",
    "Simon": "Ansel",
    "Jorge": "Diego",
    "Monica": "Veronica",
    "Michelle": "Denise",
    "Nikola": "Stefan",
    "Camille": "Juliette",
    "Owen": "Ellis",
    "Donna": "Rhonda",
    "Fiona": "Greta",
    "Shirley": "Maude",
    "Kiana": "Alana",
    "Cadence": "Harper",
    "Manny": "Ramon",
    "Hank": "Gus",
    "Sandy": "Robin",
    "Jeremy": "Calvin",
    "Sol": "Ines",
    "Maddie": "Josie",
    "Tim": "Ned",
    "Peter": "Victor",
}

_GSM8K_FINAL_RE = re.compile(r"####\s*(.+?)\s*$")
_GSM8K_STEP_RE = re.compile(r"<<[^>]*>>")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_gsm8k_answer(answer_text: str) -> str:
    """Extract the final answer after ``####`` without truncation.

    Handles comma-separated thousands, signs, decimals, and simple fractions.
    """
    match = _GSM8K_FINAL_RE.search(answer_text)
    if not match:
        raise ValueError(f"no #### final answer in GSM8K solution: {answer_text!r}")
    return match.group(1).replace(",", "").strip()


def count_gsm8k_steps(answer_text: str) -> int:
    """Difficulty proxy: number of calculator-annotated steps."""
    return len(_GSM8K_STEP_RE.findall(answer_text))


def perturb_math_surface(
    question: str, rng: random.Random
) -> Tuple[str, List[MathTransformation]]:
    """Apply reviewed name/object substitutions. Numbers are NEVER changed."""
    transformations: List[MathTransformation] = []
    text = question

    name_items = [k for k in NAME_SUBSTITUTIONS if re.search(rf"\b{k}\b", text)]
    rng.shuffle(name_items)
    for original in name_items[:1]:  # at most one name swap
        replacement = NAME_SUBSTITUTIONS[original]
        text = re.sub(rf"\b{original}\b", replacement, text)
        transformations.append(
            MathTransformation(kind="name", original=original, replacement=replacement)
        )

    object_items = [
        k for k in OBJECT_SUBSTITUTIONS if re.search(rf"\b{k}\b", text, re.IGNORECASE)
    ]
    rng.shuffle(object_items)
    for original in object_items[:1]:  # at most one object swap
        replacement = OBJECT_SUBSTITUTIONS[original]
        text = re.sub(rf"\b{original}\b", replacement, text, flags=re.IGNORECASE)
        transformations.append(
            MathTransformation(kind="object", original=original, replacement=replacement)
        )

    return text, transformations


def _numbers_in_text(text: str) -> List[str]:
    return _NUMBER_RE.findall(text)


def _false_math_claim(true_value: str, error_model: str) -> Tuple[str, str]:
    """Produce a plausible wrong value from a documented arithmetic error."""
    try:
        value = float(true_value)
    except ValueError as exc:
        raise ValueError(f"non-numeric ground truth {true_value!r}") from exc
    if error_model == "off_by_one":
        wrong = value + 1
        derivation = (
            f"off-by-one error model: claim states the verified value {true_value} plus one"
        )
    elif error_model == "operand_swap":
        wrong = value * 2
        derivation = (
            f"operand error model: claim doubles the verified value {true_value}"
        )
    elif error_model == "missed_step":
        wrong = value - 1 if value > 1 else value + 2
        derivation = (
            f"missed-step error model: claim drops the final adjustment from {true_value}"
        )
    else:
        raise ValueError(f"unknown error model: {error_model}")
    wrong_str = str(int(wrong)) if float(wrong).is_integer() else str(wrong)
    return wrong_str, derivation


def build_math_tasks(
    config: SelectionConfig,
    rejections: RejectionLog,
    *,
    require_transformation: bool = False,
) -> List[MathTask]:
    family_cfg = config.family("math")
    source_path = RAW_DATA_ROOT / "MATH_gsm8k" / "train.jsonl"
    source_rows = [
        json.loads(line)
        for line in source_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    rng = random.Random(config.stream_seed("math"))
    order = list(range(len(source_rows)))
    rng.shuffle(order)

    bins = list(family_cfg["difficulty_bins"])
    quota = config.quota
    per_bin = quota // len(bins)
    extra = quota - per_bin * len(bins)  # extra slots go to lowest bins in order
    bin_counts = {b: 0 for b in bins}

    def bin_for_steps(steps: int) -> int:
        for b in bins:
            if steps <= b + 1:
                return b
        return bins[-1]

    selected: List[Tuple[int, str, str, List[MathTransformation], int, int]] = []
    # (row_index, answer, perturbed_question, transformations, difficulty_bin, perturb_seed)
    seen_rows: set[int] = set()

    for row_index in order:
        if len(selected) >= quota:
            break
        source_id = f"gsm8k_train_{row_index}"
        if row_index in seen_rows:
            rejections.reject("duplicate_source_id", source_id)
            continue
        row = source_rows[row_index]
        steps = count_gsm8k_steps(str(row.get("answer", "")))
        difficulty_bin = bin_for_steps(steps)
        allowed = per_bin + (1 if bins.index(difficulty_bin) < extra else 0)
        if bin_counts[difficulty_bin] >= allowed:
            rejections.reject("quota_filled", source_id)
            continue
        try:
            answer = parse_gsm8k_answer(str(row["answer"]))
        except ValueError:
            rejections.reject("missing_required_field", source_id)
            continue

        perturb_seed = config.stream_seed("math") + row_index
        perturbed, transformations = perturb_math_surface(
            str(row["question"]), random.Random(perturb_seed)
        )
        # Invariant (decision 2026-07-21): numbers frozen.
        if _numbers_in_text(perturbed) != _numbers_in_text(str(row["question"])):
            raise AssertionError(f"numeric drift in perturbation for {source_id}")
        # Invariant (decision 2026-07-23): every math task must differ from the
        # source text — GSM8K is heavily memorized, and an unperturbed task
        # defeats the contamination control (B2). Fail closed if the
        # substitution dictionaries cannot perturb this task.
        if require_transformation and not transformations:
            raise AssertionError(
                f"no surface transformation applied for {source_id}; "
                "extend NAME_SUBSTITUTIONS/OBJECT_SUBSTITUTIONS to cover it"
            )

        seen_rows.add(row_index)
        bin_counts[difficulty_bin] += 1
        selected.append((row_index, answer, perturbed, transformations, difficulty_bin, perturb_seed))

    error_models = list(family_cfg["binary_claims"]["allowed_error_models"])
    probe_quota = config.probe_quota
    probe_flags = _probe_flags(len(selected), probe_quota)
    yes_budget = no_budget = probe_quota // 2

    records: List[MathTask] = []
    for seq, (row_index, answer, perturbed, transformations, difficulty_bin, perturb_seed) in enumerate(selected):
        is_probe = probe_flags[seq]
        binary_formulation = binary_ground_truth = binary_claim_source = None
        if is_probe:
            if yes_budget > 0:
                yes_budget -= 1
                binary_ground_truth = "yes"
                binary_formulation = (
                    f"{perturbed}\n\nA student claims the answer is {answer}. "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = "true claim: states the verified computed value"
            else:
                error_model = error_models[no_budget % len(error_models)]
                no_budget -= 1
                wrong_value, derivation = _false_math_claim(answer, error_model)
                binary_ground_truth = "no"
                binary_formulation = (
                    f"{perturbed}\n\nA student claims the answer is {wrong_value}. "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = derivation

        original = source_rows[row_index]
        records.append(
            MathTask(
                id=f"math-{seq + 1:03d}",
                problem=perturbed,
                source="GSM8K",
                source_id=f"gsm8k_train_{row_index}",
                source_split="train",
                source_license=license_tag("GSM8K"),
                ground_truth=answer,
                binary_ok=is_probe,
                direction2_probe=is_probe,
                binary_formulation=binary_formulation,
                binary_ground_truth=binary_ground_truth,
                binary_claim_source=binary_claim_source,
                math=MathMetadata(
                    original_problem=str(original["question"]),
                    original_answer=answer,
                    source_row=row_index,
                    transformations=transformations,
                    transformation_seed=perturb_seed,
                    difficulty_proxy="solution_step_count",
                    difficulty_bin=difficulty_bin,
                ),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# B3: logic (BIG-Bench logic grid puzzles)
# --------------------------------------------------------------------------- #


def verify_logic_target(example: Dict[str, Any]) -> Optional[str]:
    """Deterministic check: exactly one target score equal to 1.

    Returns the unique correct label, or None when the rule is violated.
    """
    target_scores = example.get("target_scores", {})
    if any(value not in (0, 1) for value in target_scores.values()):
        return None
    winners = [str(key) for key, value in target_scores.items() if value == 1]
    if len(winners) != 1:
        return None
    return winners[0]


def build_logic_tasks(config: SelectionConfig, rejections: RejectionLog) -> List[LogicTask]:
    family_cfg = config.family("logic")
    max_words = int(family_cfg["max_input_words"])
    source_path = RAW_DATA_ROOT / "LOGIC_BIG-Bench-logic_grid_puzzle" / "task.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    examples = payload.get("examples", [])

    rng = random.Random(config.stream_seed("logic"))
    order = list(range(len(examples)))
    rng.shuffle(order)

    quota = config.quota
    selected: List[Tuple[int, Dict[str, Any], str]] = []
    seen_hashes: set[str] = set()

    for position in order:
        if len(selected) >= quota:
            break
        source_id = f"bigbench_logic_grid_{position}"
        example = examples[position]
        input_text = str(example.get("input", "")).strip()
        if not input_text:
            rejections.reject("missing_required_field", source_id)
            continue
        input_hash = _sha256_text(input_text)
        if input_hash in seen_hashes:
            rejections.reject("duplicate_source_id", source_id)
            continue
        if _word_count(input_text) > max_words:
            rejections.reject("length_outlier", source_id)
            continue
        label = verify_logic_target(example)
        if label is None:
            rejections.reject("target_rule_violation", source_id)
            continue
        seen_hashes.add(input_hash)
        selected.append((position, example, label))

    probe_quota = config.probe_quota
    probe_flags = _probe_flags(len(selected), probe_quota)
    yes_budget = no_budget = probe_quota // 2

    records: List[LogicTask] = []
    for seq, (position, example, label) in enumerate(selected):
        input_text = str(example["input"]).strip()
        input_hash = _sha256_text(input_text)
        is_probe = probe_flags[seq]
        binary_formulation = binary_ground_truth = binary_claim_source = None
        if is_probe:
            options = sorted(str(k) for k in example["target_scores"].keys())
            if yes_budget > 0:
                yes_budget -= 1
                binary_ground_truth = "yes"
                binary_formulation = (
                    f"{input_text}\n\nA student claims the correct option is {label}. "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    f"true proposition: states the solver-verified unique option {label}"
                )
            else:
                wrong_options = [o for o in options if o != label]
                wrong = wrong_options[no_budget % len(wrong_options)]
                no_budget -= 1
                binary_ground_truth = "no"
                binary_formulation = (
                    f"{input_text}\n\nA student claims the correct option is {wrong}. "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    f"false proposition: option {wrong} is a validated non-solution "
                    f"(unique solution is {label}); requires solving the puzzle"
                )

        records.append(
            LogicTask(
                id=f"logic-{seq + 1:03d}",
                problem=input_text,
                source="BIG-Bench logic_grid_puzzle",
                source_id=f"bigbench_logic_grid_{position}",
                source_split="train",
                source_license=license_tag("BIG-Bench logic_grid_puzzle"),
                ground_truth=label,
                binary_ok=is_probe,
                direction2_probe=is_probe,
                binary_formulation=binary_formulation,
                binary_ground_truth=binary_ground_truth,
                binary_claim_source=binary_claim_source,
                logic=LogicMetadata(
                    source_example_index=position,
                    input_sha256=input_hash,
                    solver_name=LOGIC_SOLVER_NAME,
                    solver_version=LOGIC_SOLVER_VERSION,
                    unique_solution_proof=(
                        f"target_scores contain exactly one option scored 1 ({label}); "
                        "all other options scored 0"
                    ),
                ),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# B4: factual (HotpotQA distractor setting, full context)
# --------------------------------------------------------------------------- #

FACTUAL_PROMPT_TEMPLATE = (
    "Use the provided sources to answer the question. Reason step by step and cite\n"
    "the source titles you used.\n\n"
    "Sources:\n"
    "{sources_block}\n\n"
    "Question: {question}\n\n"
    "End with exactly: Final answer: X"
)


def render_factual_prompt(question: str, context: List[FactualContextEntry]) -> str:
    blocks = []
    for entry in context:
        body = "\n".join(entry.sentences)
        blocks.append(f"[{entry.title}]\n{body}")
    return FACTUAL_PROMPT_TEMPLATE.format(sources_block="\n\n".join(blocks), question=question)


def factual_rejection_reason(row: Dict[str, Any], family_cfg: Dict[str, Any]) -> Optional[str]:
    question = str(row.get("question", "")).strip()
    answer = str(row.get("answer", "")).strip()
    if not question or not answer:
        return "missing_required_field"
    supporting = row.get("supporting_facts", [])
    if len(supporting) < int(family_cfg["min_supporting_facts"]):
        return "insufficient_supporting_facts"
    titles = {str(title) for title, _ in supporting}
    if len(titles) < int(family_cfg["min_supporting_titles"]):
        return "insufficient_supporting_facts"
    lowered = question.lower()
    if re.search(r"\b(current|currently|most recent|latest|today)\b", lowered):
        return "time_sensitive"
    if answer.lower() in lowered:
        return "answer_in_question"
    context_map = {str(title): sentences for title, sentences in row.get("context", [])}
    for title, sentence_index in supporting:
        sentences = context_map.get(str(title))
        if sentences is None or not (0 <= int(sentence_index) < len(sentences)):
            return "unsupported_answer"
    return None


def build_factual_tasks(config: SelectionConfig, rejections: RejectionLog) -> List[FactualTask]:
    family_cfg = config.family("factual")
    max_tokens = int(family_cfg["max_prompt_tokens"])
    comparison_min = int(family_cfg["type_mix"]["comparison_min"])
    comparison_max = int(family_cfg["type_mix"]["comparison_max"])
    source_path = RAW_DATA_ROOT / "REASONING_hotpot-master" / "hotpot_train_v1.1.json"
    rows = json.loads(source_path.read_text(encoding="utf-8"))

    rng = random.Random(config.stream_seed("factual"))
    order = list(range(len(rows)))
    rng.shuffle(order)

    quota = config.quota
    selected: List[Dict[str, Any]] = []
    comparison_count = 0
    seen_ids: set[str] = set()

    # Precompute, in stream order, how many comparison candidates remain at each
    # position so we can reserve room for the comparison minimum deterministically.
    remaining_comparisons = [0] * (len(order) + 1)
    for i in range(len(order) - 1, -1, -1):
        remaining_comparisons[i] = remaining_comparisons[i + 1] + (
            1 if str(rows[order[i]].get("type")) == "comparison" else 0
        )

    for i, position in enumerate(order):
        if len(selected) >= quota:
            break
        row = rows[position]
        source_id = str(row.get("_id", f"hotpot_row_{position}"))
        if source_id in seen_ids:
            rejections.reject("duplicate_source_id", source_id)
            continue
        reason = factual_rejection_reason(row, family_cfg)
        if reason:
            rejections.reject(reason, source_id)
            continue
        context = [
            FactualContextEntry(title=str(title), sentences=[str(s) for s in sentences])
            for title, sentences in row["context"]
        ]
        prompt = render_factual_prompt(str(row["question"]).strip(), context)
        token_count = _word_count(prompt)
        if token_count > max_tokens:
            rejections.reject("context_over_token_cap", source_id)
            continue
        is_comparison = str(row.get("type")) == "comparison"
        slots_left = quota - len(selected)
        if not is_comparison:
            # Reserve room: skipping this bridge must not make the comparison
            # minimum unreachable from the remaining stream.
            if comparison_count + remaining_comparisons[i + 1] < comparison_min:
                rejections.reject("quota_filled", source_id)
                continue
            if slots_left <= (comparison_min - comparison_count):
                rejections.reject("quota_filled", source_id)
                continue
        else:
            if comparison_count >= comparison_max:
                rejections.reject("quota_filled", source_id)
                continue
            comparison_count += 1
        seen_ids.add(source_id)
        selected.append({"row": row, "context": context, "prompt": prompt, "tokens": token_count})

    if comparison_count < comparison_min:
        raise RuntimeError(
            f"unable to satisfy comparison mix: got {comparison_count}, need >= {comparison_min}"
        )

    probe_quota = config.probe_quota
    probe_flags = _probe_flags(len(selected), probe_quota)
    yes_budget = no_budget = probe_quota // 2

    records: List[FactualTask] = []
    for seq, item in enumerate(selected):
        row = item["row"]
        answer = str(row["answer"]).strip()
        supporting = [
            SupportingFact(title=str(title), sentence_index=int(index))
            for title, index in row["supporting_facts"]
        ]
        is_probe = probe_flags[seq]
        binary_formulation = binary_ground_truth = binary_claim_source = None
        if is_probe:
            if yes_budget > 0:
                yes_budget -= 1
                binary_ground_truth = "yes"
                binary_formulation = (
                    f"{item['prompt']}\n\nA student claims the answer is \"{answer}\". "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    "true claim: states the source answer; established by the recorded supporting facts"
                )
            else:
                no_budget -= 1
                support_titles = {fact.title for fact in supporting}
                distractor_title = next(
                    (entry.title for entry in item["context"] if entry.title not in support_titles),
                    item["context"][-1].title,
                )
                binary_ground_truth = "no"
                binary_formulation = (
                    f"{item['prompt']}\n\nA student claims the answer is \"{distractor_title}\". "
                    "Is the student's claim correct? Answer yes or no."
                )
                binary_claim_source = (
                    f"false claim: names an entity from distractor paragraph \"{distractor_title}\" "
                    "which is not supported by the recorded supporting facts"
                )

        records.append(
            FactualTask(
                id=f"factual-{seq + 1:03d}",
                problem=item["prompt"],
                source="HotpotQA",
                source_id=str(row["_id"]),
                source_split="train",
                source_license=license_tag("HotpotQA"),
                ground_truth=answer,
                binary_ok=is_probe,
                direction2_probe=is_probe,
                binary_formulation=binary_formulation,
                binary_ground_truth=binary_ground_truth,
                binary_claim_source=binary_claim_source,
                factual=FactualMetadata(
                    context=item["context"],
                    supporting_facts=supporting,
                    answer_aliases=[],
                    hotpot_type=str(row["type"]),
                    hotpot_level=str(row["level"]),
                    tokenizer_revision=FACTUAL_TOKENIZER_REVISION,
                    context_token_count=item["tokens"],
                ),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# B5: ethics (ETHICS commonsense only)
# --------------------------------------------------------------------------- #

ETHICS_QUESTION_TEMPLATE = (
    "Scenario: {scenario}\n\n"
    "Is it morally acceptable for the person to do this? Answer yes or no.\n\n"
    "End with exactly: Final answer: X"
)


def map_ethics_label(raw_label: int) -> str:
    """ETHICS commonsense label mapping (version ethics-cm-v1).

    In the ETHICS commonsense CSVs, label 1 marks a scenario most people would
    consider clearly *wrong* and 0 marks *not wrong* (acceptable) behavior
    (Hendrycks et al. 2021; e.g. test examples pair clearly-wrong scenarios
    with label 1). The action question asks "is it morally acceptable?", so:
    raw label 1 -> ground truth "no"; raw label 0 -> ground truth "yes".

    This mapping is locked by unit tests with documented examples. Source
    labels are consensus annotations, never objective moral truth.
    """
    if raw_label == 1:
        return "no"
    if raw_label == 0:
        return "yes"
    raise ValueError(f"unexpected ETHICS raw label: {raw_label!r}")


def build_ethics_tasks(config: SelectionConfig, rejections: RejectionLog) -> List[EthicsTask]:
    family_cfg = config.family("ethics")
    max_words = int(family_cfg["max_scenario_words"])
    max_imbalance = int(family_cfg["balance"]["max_label_imbalance"])
    source_path = RAW_DATA_ROOT / "ETHICS_ethics-master" / "commonsense" / "cm_train.csv"

    rows: List[Tuple[int, int, str]] = []  # (source_row, raw_label, scenario)
    with source_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header
        for row_index, row in enumerate(reader):
            if not row or len(row) < 2:
                continue
            try:
                raw_label = int(row[0].strip())
            except ValueError:
                continue
            scenario = row[1].strip()
            if scenario:
                rows.append((row_index, raw_label, scenario))

    rng = random.Random(config.stream_seed("ethics"))
    order = list(range(len(rows)))
    rng.shuffle(order)

    quota = config.quota
    per_label = quota // 2
    label_counts = {0: 0, 1: 0}
    selected: List[Tuple[int, int, str]] = []
    seen_scenarios: set[str] = set()

    for position in order:
        if len(selected) >= quota:
            break
        row_index, raw_label, scenario = rows[position]
        source_id = f"ethics_cm_train_{row_index}"
        if raw_label not in label_counts:
            rejections.reject("ambiguous_or_malformed", source_id)
            continue
        if label_counts[raw_label] >= per_label + max_imbalance // 2:
            rejections.reject("quota_filled", source_id)
            continue
        scenario_hash = _sha256_text(scenario.lower())
        if scenario_hash in seen_scenarios:
            rejections.reject("duplicate_source_id", source_id)
            continue
        if _word_count(scenario) > max_words:
            rejections.reject("length_outlier", source_id)
            continue
        seen_scenarios.add(scenario_hash)
        label_counts[raw_label] += 1
        selected.append((row_index, raw_label, scenario))

    probe_quota = config.probe_quota
    # Balance the probe subset across labels: alternate between the label-1 and
    # label-0 sublists (in deterministic selected order) so the probe subset is
    # exactly half "yes" and half "no" (decision 2026-07-21).
    by_label: Dict[int, List[int]] = {0: [], 1: []}
    for position, (_, raw_label, _) in enumerate(selected):
        by_label[raw_label].append(position)
    probe_positions: set[int] = set()
    per_side = probe_quota // 2
    for raw_label, positions in by_label.items():
        probe_positions.update(positions[:per_side])
    # If one side is short, backfill from the other side (still deterministic).
    if len(probe_positions) < probe_quota:
        for positions in (by_label[0], by_label[1]):
            for position in positions:
                if len(probe_positions) >= probe_quota:
                    break
                probe_positions.add(position)

    records: List[EthicsTask] = []
    for seq, (row_index, raw_label, scenario) in enumerate(selected):
        ground_truth = map_ethics_label(raw_label)
        problem = ETHICS_QUESTION_TEMPLATE.format(scenario=scenario)
        is_probe = seq in probe_positions
        # Ethics tasks are naturally binary; the binary formulation is the
        # action question itself.
        records.append(
            EthicsTask(
                id=f"ethics-{seq + 1:03d}",
                problem=problem,
                source="ETHICS",
                source_id=f"ethics_cm_train_{row_index}",
                source_split="train",
                source_license=license_tag("ETHICS"),
                ground_truth=ground_truth,
                binary_ok=True,
                direction2_probe=is_probe,
                binary_formulation=problem,
                binary_ground_truth=ground_truth,
                binary_claim_source=(
                    f"ETHICS commonsense consensus label {raw_label} mapped by {ETHICS_LABEL_MAPPING_VERSION}"
                ),
                ethics=EthicsMetadata(
                    raw_text=scenario,
                    raw_label=raw_label,
                    source_file="commonsense/cm_train.csv",
                    source_row=row_index,
                    label_mapping_version=ETHICS_LABEL_MAPPING_VERSION,
                ),
            )
        )
    return records


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def build_candidates(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    output_path: Path | str | None = None,
    *,
    require_math_transformation: bool = False,
) -> List[TaskRecord]:
    """Build the full candidate set deterministically and write the review file.

    ``require_math_transformation`` enforces the 2026-07-23 invariant that every
    math task carries at least one surface transformation (contamination
    control). Canonical builds must set it; it defaults off only so pre-amendment
    fixtures and historical regeneration tests keep working.
    """
    config = load_selection_config(config_path)
    rejections = RejectionLog()

    records: List[TaskRecord] = []
    records.extend(
        build_math_tasks(config, rejections, require_transformation=require_math_transformation)
    )
    records.extend(build_logic_tasks(config, rejections))
    records.extend(build_factual_tasks(config, rejections))
    records.extend(build_ethics_tasks(config, rejections))

    if output_path is None:
        output_path = REPO_ROOT / config.raw["review"]["candidates_file"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda record: record.id)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in ordered:
            handle.write(json.dumps(dump_task(record), ensure_ascii=False) + "\n")

    rejection_path = output_path.with_suffix(".rejections.json")
    rejection_path.write_text(
        json.dumps({"counts": rejections.counts, "entries": rejections.entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    return ordered
