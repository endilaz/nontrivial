"""Tests for the Milestone B2–B5 deterministic task-set builder."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from src.dataset_licenses import UnknownDatasetLicenseError, license_for_source
from src.task_schema import load_task_set
from src.task_set import (
    RejectionLog,
    build_candidates,
    build_ethics_tasks,
    build_logic_tasks,
    count_gsm8k_steps,
    load_selection_config,
    map_ethics_label,
    parse_gsm8k_answer,
    perturb_math_surface,
    verify_logic_target,
)
import random


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("tasks") / "candidates.jsonl"
    records = build_candidates(output_path=out)
    return records, out


# --------------------------------------------------------------------------- #
# Acceptance-shape tests (B7 gate inputs)
# --------------------------------------------------------------------------- #


def test_builds_200_tasks_50_per_type(built):
    records, _ = built
    assert len(records) == 200
    assert Counter(r.task_type for r in records) == {
        "math": 50,
        "logic": 50,
        "factual": 50,
        "ethics": 50,
    }


def test_probe_subsets_balanced_10_10(built):
    records, _ = built
    for task_type in ("math", "logic", "factual", "ethics"):
        probes = [r for r in records if r.task_type == task_type and r.direction2_probe]
        assert len(probes) == 20, task_type
        labels = Counter(r.binary_ground_truth for r in probes)
        assert labels == {"yes": 10, "no": 10}, task_type


def test_canonical_task_type_is_factual(built):
    records, _ = built
    assert not any(r.task_type == "reasoning" for r in records)
    assert sum(1 for r in records if r.task_type == "factual") == 50


def test_no_unknown_licenses(built):
    records, _ = built
    for record in records:
        assert "unknown" not in record.source_license.lower()
        assert record.source_id


def test_deterministic_byte_identical(tmp_path):
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    build_candidates(output_path=first)
    build_candidates(output_path=second)
    assert first.read_bytes() == second.read_bytes()


def test_candidates_validate_against_schema(built):
    _, path = built
    records = load_task_set(path)  # raises on any invalid record
    assert len(records) == 200
    assert all(r.review_status == "pending" for r in records)


# --------------------------------------------------------------------------- #
# B2 math specifics
# --------------------------------------------------------------------------- #


def test_math_numbers_frozen(built):
    records, _ = built
    for record in records:
        if record.task_type != "math":
            continue
        original_nums = re.findall(r"\d+(?:\.\d+)?", record.math.original_problem)
        new_nums = re.findall(r"\d+(?:\.\d+)?", record.problem)
        assert original_nums == new_nums, record.id


def test_math_ground_truth_matches_original(built):
    records, _ = built
    for record in records:
        if record.task_type != "math":
            continue
        assert record.ground_truth == record.math.original_answer


def test_math_false_claims_are_actually_wrong(built):
    records, _ = built
    for record in records:
        if record.task_type != "math" or not record.direction2_probe:
            continue
        if record.binary_ground_truth == "no":
            claimed = re.search(r"claims the answer is ([\d.]+)\.", record.binary_formulation)
            assert claimed, record.id
            assert claimed.group(1) != record.ground_truth, record.id
            assert record.binary_claim_source
        else:
            assert record.ground_truth in record.binary_formulation


def test_parse_gsm8k_answer_formats():
    assert parse_gsm8k_answer("work\n#### 72") == "72"
    assert parse_gsm8k_answer("#### 1,234") == "1234"
    assert parse_gsm8k_answer("#### -3.5") == "-3.5"
    assert parse_gsm8k_answer("#### 3/4") == "3/4"
    with pytest.raises(ValueError):
        parse_gsm8k_answer("no marker here")


def test_count_gsm8k_steps():
    assert count_gsm8k_steps("<<48/2=24>>24 then <<48+24=72>>72\n#### 72") == 2
    assert count_gsm8k_steps("plain text") == 0


def test_perturbation_never_changes_numbers():
    rng = random.Random(0)
    question = "Natalia sold 48 clips to 12 friends and 3 apples. How many in total?"
    perturbed, transformations = perturb_math_surface(question, rng)
    assert re.findall(r"\d+", perturbed) == ["48", "12", "3"]
    assert all(t.kind in ("name", "object", "wording") for t in transformations)


def test_math_difficulty_bins_cover_quota(built):
    records, _ = built
    bins = Counter(r.math.difficulty_bin for r in records if r.task_type == "math")
    assert sum(bins.values()) == 50
    assert max(bins.values()) - min(bins.values()) <= 1


# --------------------------------------------------------------------------- #
# B3 logic specifics
# --------------------------------------------------------------------------- #


def test_logic_unique_target_verified(built):
    records, _ = built
    for record in records:
        if record.task_type != "logic":
            continue
        assert record.ground_truth
        assert "exactly one option scored 1" in record.logic.unique_solution_proof
        assert len(record.logic.input_sha256) == 64


def test_logic_false_proposition_is_non_solution(built):
    records, _ = built
    for record in records:
        if record.task_type != "logic" or not record.direction2_probe:
            continue
        if record.binary_ground_truth == "no":
            claimed = re.search(r"correct option is (\S+)\.", record.binary_formulation)
            assert claimed and claimed.group(1) != record.ground_truth


def test_verify_logic_target_rejects_ambiguous():
    assert verify_logic_target({"target_scores": {"a": 1, "b": 1}}) is None
    assert verify_logic_target({"target_scores": {"a": 0, "b": 0}}) is None
    assert verify_logic_target({"target_scores": {"a": 0, "b": 1}}) == "b"


# --------------------------------------------------------------------------- #
# B4 factual specifics
# --------------------------------------------------------------------------- #


def test_factual_context_and_provenance_retained(built):
    records, _ = built
    for record in records:
        if record.task_type != "factual":
            continue
        assert len(record.factual.supporting_facts) >= 2
        assert len({f.title for f in record.factual.supporting_facts}) >= 2
        assert record.factual.hotpot_type in ("bridge", "comparison")
        assert record.factual.hotpot_level in ("easy", "medium", "hard")
        # Full context is in the record and in the rendered prompt.
        for entry in record.factual.context:
            assert f"[{entry.title}]" in record.problem
        assert record.problem.endswith("End with exactly: Final answer: X")


def test_factual_comparison_mix_within_bounds(built):
    records, _ = built
    config = load_selection_config()
    lo = config.family("factual")["type_mix"]["comparison_min"]
    hi = config.family("factual")["type_mix"]["comparison_max"]
    count = sum(1 for r in records if r.task_type == "factual" and r.factual.hotpot_type == "comparison")
    assert lo <= count <= hi


def test_factual_false_claim_uses_distractor(built):
    records, _ = built
    for record in records:
        if record.task_type != "factual" or not record.direction2_probe:
            continue
        if record.binary_ground_truth == "no":
            assert "distractor" in record.binary_claim_source
            assert record.ground_truth not in record.binary_formulation.split("A student claims")[1]


# --------------------------------------------------------------------------- #
# B5 ethics specifics
# --------------------------------------------------------------------------- #


def test_ethics_label_mapping():
    # Documented ETHICS commonsense semantics: label 1 = clearly wrong,
    # label 0 = not wrong (Hendrycks et al. 2021). The action question asks
    # "is it morally acceptable?", so 1 -> "no", 0 -> "yes".
    assert map_ethics_label(1) == "no"
    assert map_ethics_label(0) == "yes"
    with pytest.raises(ValueError):
        map_ethics_label(2)


def test_ethics_commonsense_only_and_balanced(built):
    records, _ = built
    ethics = [r for r in records if r.task_type == "ethics"]
    assert len(ethics) == 50
    assert all(r.ethics.source_file == "commonsense/cm_train.csv" for r in ethics)
    labels = Counter(r.ethics.raw_label for r in ethics)
    assert abs(labels[0] - labels[1]) <= 2
    assert all(r.scoring_policy == "source_label_agreement" for r in ethics)
    # Ethics tasks are naturally binary.
    assert all(r.binary_ok for r in ethics)
    assert all(r.binary_formulation == r.problem for r in ethics)


def test_ethics_raw_text_preserved(built):
    records, _ = built
    for record in records:
        if record.task_type == "ethics":
            assert record.ethics.raw_text in record.problem


# --------------------------------------------------------------------------- #
# B6 license registry
# --------------------------------------------------------------------------- #


def test_license_registry_covers_all_sources():
    for source in ("GSM8K", "BIG-Bench logic_grid_puzzle", "HotpotQA", "ETHICS"):
        facts = license_for_source(source)
        assert facts["license"] and facts["url"] and facts["retrieved"]
    with pytest.raises(UnknownDatasetLicenseError):
        license_for_source("SomeNewDataset")


def test_hotpot_data_and_code_licenses_not_conflated():
    facts = license_for_source("HotpotQA")
    assert facts["license"] == "CC BY-SA 4.0"
    assert facts["code_license"] == "Apache-2.0"
