"""Build a deterministic benchmark task set for Phase 1.

The builder samples 50 problems from each of four task families:
- ethics
- logic
- math (GSM8K-derived, with surface-level perturbation)
- reasoning (Hotpot-style multi-hop factual questions)

Output is written as JSONL, with metadata fields required by the benchmark notes:
problem id, task type, source, ground truth, license, answer format, and
binary_ok / binary_formulation flags.
"""

from __future__ import annotations

import csv
import json
import random
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_ROOT = REPO_ROOT / "data" / "tasks"


def build_task_set(output_path: str | Path | None = None, seed: int = 7) -> list[dict[str, Any]]:
    """Create and save the benchmark task set.

    Args:
        output_path: Destination JSONL file. Defaults to ``data/tasks/task_set.jsonl``.
        seed: Deterministic random seed for task selection and perturbations.
    """

    if output_path is None:
        output_path = TASKS_ROOT / "task_set.jsonl"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    tasks: list[dict[str, Any]] = []

    tasks.extend(_sample_math_tasks(rng, count=50))
    tasks.extend(_sample_logic_tasks(rng, count=50))
    tasks.extend(_sample_reasoning_tasks(rng, count=50))
    tasks.extend(_sample_ethics_tasks(rng, count=50))

    rng.shuffle(tasks)

    with output_path.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")

    return tasks


def _sample_math_tasks(rng: random.Random, count: int) -> list[dict[str, Any]]:
    source_path = TASKS_ROOT / "MATH_gsm8k" / "train.jsonl"
    rows = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    sampled_rows = rng.sample(rows, k=min(count, len(rows)))
    tasks: list[dict[str, Any]] = []
    for idx, row in enumerate(sampled_rows, start=1):
        question = str(row.get("question", "")).strip()
        answer = _extract_final_answer(str(row.get("answer", "")))
        perturbation_seed = seed_for_index(seed=7 + idx, index=idx)
        perturbed_question = perturb_math_question(question, perturbation_seed)
        binary_ok = idx <= 20
        binary_formulation = None
        if binary_ok:
            binary_formulation = build_binary_math_formulation(perturbed_question, answer)

        tasks.append(
            {
                "id": f"math-{idx:03d}",
                "task_type": "math",
                "problem": perturbed_question,
                "source": "GSM8K",
                "original_id": f"gsm8k_train_{idx}",
                "ground_truth": answer,
                "license": "unknown",
                "answer_format": "number",
                "binary_ok": binary_ok,
                "binary_formulation": binary_formulation,
                "perturbation_seed": perturbation_seed,
                "perturbation_types": ["entity_names", "numbers", "wording"],
            }
        )

    return tasks


def _sample_logic_tasks(rng: random.Random, count: int) -> list[dict[str, Any]]:
    source_path = TASKS_ROOT / "LOGIC_BIG-Bench-logic_grid_puzzle" / "task.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    examples = payload.get("examples", [])
    sampled_examples = rng.sample(examples, k=min(count, len(examples)))

    tasks: list[dict[str, Any]] = []
    for idx, example in enumerate(sampled_examples, start=1):
        input_text = str(example.get("input", "")).strip()
        target_scores = example.get("target_scores", {})
        label = next((key for key, value in target_scores.items() if value == 1), None)
        binary_ok = idx <= 20
        binary_formulation = None
        if binary_ok:
            binary_formulation = f"Is the correct answer choice {label}?"

        tasks.append(
            {
                "id": f"logic-{idx:03d}",
                "task_type": "logic",
                "problem": input_text,
                "source": "BIG-Bench logic_grid_puzzle",
                "ground_truth": label,
                "license": "unknown",
                "answer_format": "short_text",
                "binary_ok": binary_ok,
                "binary_formulation": binary_formulation,
                "perturbation_seed": None,
                "perturbation_types": [],
            }
        )

    return tasks


def _sample_reasoning_tasks(rng: random.Random, count: int) -> list[dict[str, Any]]:
    source_path = TASKS_ROOT / "REASONING_hotpot-master" / "hotpot_train_v1.1.json"
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    sampled_rows = rng.sample(rows, k=min(count, len(rows)))

    tasks: list[dict[str, Any]] = []
    for idx, row in enumerate(sampled_rows, start=1):
        question = str(row.get("question", "")).strip()
        answer = str(row.get("answer", "")).strip()
        binary_ok = idx <= 20
        binary_formulation = None
        if binary_ok:
            binary_formulation = f"Is the answer {answer}?"

        tasks.append(
            {
                "id": f"reasoning-{idx:03d}",
                "task_type": "reasoning",
                "problem": question,
                "source": "HotpotQA",
                "ground_truth": answer,
                "license": "unknown",
                "answer_format": "short_text",
                "binary_ok": binary_ok,
                "binary_formulation": binary_formulation,
                "perturbation_seed": None,
                "perturbation_types": [],
            }
        )

    return tasks


def _sample_ethics_tasks(rng: random.Random, count: int) -> list[dict[str, Any]]:
    rows: list[tuple[str, str]] = []
    csv_paths = [
        TASKS_ROOT / "ETHICS_ethics-master" / "commonsense" / "cm_train.csv",
        TASKS_ROOT / "ETHICS_ethics-master" / "deontology" / "deontology_train.csv",
    ]
    for csv_path in csv_paths:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            for row in reader:
                if not row:
                    continue
                label = row[0].strip()
                scenario = row[1].strip() if len(row) > 1 else ""
                if scenario:
                    rows.append((label, scenario))

    sampled_rows = rng.sample(rows, k=min(count, len(rows)))
    tasks: list[dict[str, Any]] = []
    for idx, (label, scenario) in enumerate(sampled_rows, start=1):
        ground_truth = "yes" if label == "1" else "no"
        problem = f"Scenario: {scenario}\nShould the person do this? Answer yes or no."
        tasks.append(
            {
                "id": f"ethics-{idx:03d}",
                "task_type": "ethics",
                "problem": problem,
                "source": "ETHICS",
                "ground_truth": ground_truth,
                "license": "unknown",
                "answer_format": "yes_no",
                "binary_ok": True,
                "binary_formulation": problem,
                "perturbation_seed": None,
                "perturbation_types": [],
            }
        )

    return tasks


def _extract_final_answer(answer_text: str) -> str:
    match = re.search(r"####\s*([+-]?\d+(?:\.\d+)?)", answer_text)
    if match:
        return match.group(1)

    match = re.search(r"([+-]?\d+(?:\.\d+)?)", answer_text)
    if match:
        return match.group(1)

    return answer_text.strip()


def perturb_math_question(question: str, perturbation_seed: int) -> str:
    """Apply a small surface-level perturbation while preserving structure."""

    text = question
    rng = random.Random(perturbation_seed)

    # Replace names with seeded aliases.
    name_map = {
        "Alice": "Mina",
        "Bob": "Owen",
        "Carol": "Nina",
        "Dan": "Theo",
        "Emma": "Lila",
        "Frank": "Gabe",
        "Sarah": "Talia",
        "Tom": "Leo",
        "John": "Noah",
        "Mary": "Leah",
    }
    for old_name, new_name in name_map.items():
        if old_name in text:
            text = text.replace(old_name, new_name, 1)

    # Replace common objects with nearby alternatives.
    object_map = {
        "apples": "pears",
        "books": "notebooks",
        "cars": "bikes",
        "pencils": "markers",
        "cookies": "brownies",
        "wallet": "bag",
        "store": "market",
    }
    for old_obj, new_obj in object_map.items():
        if old_obj in text.lower():
            text = re.sub(rf"\b{old_obj}\b", new_obj, text, flags=re.IGNORECASE)

    # Subtly perturb numeric values while preserving the overall structure.
    def replace_number(match: re.Match[str]) -> str:
        value = int(match.group(0))
        delta = 1 + (rng.randrange(0, 4))
        new_value = value + delta
        return str(new_value)

    text = re.sub(r"\b\d+\b", replace_number, text)

    return text


def build_binary_math_formulation(question: str, answer: str) -> str:
    """Create a simple yes/no formulation for a math problem."""

    lowered = question.lower()
    if "cost" in lowered or "pay" in lowered or "afford" in lowered or "money" in lowered:
        return "Can the person afford the purchase?"
    if "left" in lowered or "remain" in lowered or "after" in lowered:
        return "Is the remaining amount positive?"
    if answer.isdigit() or re.fullmatch(r"[+-]?\d+(?:\.\d+)?", answer):
        return f"Is the final answer greater than 0?"
    return "Is the final answer valid?"


def seed_for_index(seed: int, index: int) -> int:
    return seed + index * 17
