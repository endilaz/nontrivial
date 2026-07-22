"""Tests for the Milestone B1 task schema (src/task_schema.py)."""

import json

import pytest
from pydantic import ValidationError

from src import schemas
from src.task_schema import (
    EthicsMetadata,
    EthicsTask,
    FactualContextEntry,
    FactualMetadata,
    FactualTask,
    SupportingFact,
    dump_task,
    load_task_set,
    parse_task,
    write_task_set,
)


def _ethics_payload(**overrides):
    payload = {
        "schema": "task_record",
        "schema_version": schemas.current_version("task_record"),
        "id": "ethics-001",
        "task_type": "ethics",
        "problem": "Scenario: x. Is it morally acceptable? Answer yes or no.",
        "source": "ETHICS",
        "source_id": "ethics_cm_train_0",
        "source_split": "train",
        "source_license": "MIT (https://example.com)",
        "ground_truth": "no",
        "answer_format": "yes_no",
        "scoring_policy": "source_label_agreement",
        "binary_ok": True,
        "direction2_probe": False,
        "binary_formulation": "Is it morally acceptable?",
        "binary_ground_truth": "no",
        "binary_claim_source": "consensus label 1 mapped by ethics-cm-v1",
        "review_status": "pending",
        "reviewer_notes": None,
        "ethics": {
            "raw_text": "x",
            "raw_label": 1,
            "source_file": "commonsense/cm_train.csv",
            "source_row": 0,
            "label_mapping_version": "ethics-cm-v1",
        },
    }
    payload.update(overrides)
    return payload


def _factual_payload(**overrides):
    context = [
        {"title": "A", "sentences": ["s0", "s1"]},
        {"title": "B", "sentences": ["t0"]},
    ]
    payload = {
        "schema": "task_record",
        "schema_version": schemas.current_version("task_record"),
        "id": "factual-001",
        "task_type": "factual",
        "problem": "Use the provided sources...",
        "source": "HotpotQA",
        "source_id": "abc123",
        "source_split": "train",
        "source_license": "CC BY-SA 4.0 (https://example.com)",
        "ground_truth": "answer",
        "answer_format": "short_text",
        "scoring_policy": "normalized_text",
        "binary_ok": False,
        "direction2_probe": False,
        "binary_formulation": None,
        "binary_ground_truth": None,
        "binary_claim_source": None,
        "review_status": "pending",
        "reviewer_notes": None,
        "factual": {
            "context": context,
            "supporting_facts": [
                {"title": "A", "sentence_index": 0},
                {"title": "B", "sentence_index": 0},
            ],
            "answer_aliases": [],
            "hotpot_type": "bridge",
            "hotpot_level": "medium",
            "tokenizer_revision": "whitespace-wordcount-proxy-v1",
            "context_token_count": 42,
        },
    }
    payload.update(overrides)
    return payload


def test_ethics_record_round_trip():
    record = parse_task(_ethics_payload())
    assert isinstance(record, EthicsTask)
    assert record.ethics.raw_label == 1
    dumped = dump_task(record)
    assert dumped["schema_version"] == schemas.current_version("task_record")


def test_binary_ok_requires_binary_fields():
    with pytest.raises(ValidationError):
        parse_task(_ethics_payload(binary_formulation=None))


def test_direction2_probe_requires_binary_ok():
    with pytest.raises(ValidationError):
        parse_task(_ethics_payload(direction2_probe=True, binary_ok=False))


def test_id_prefix_must_match_task_type():
    with pytest.raises(ValidationError):
        parse_task(_ethics_payload(id="math-001"))


def test_unknown_schema_version_fails_closed():
    payload = _ethics_payload(schema_version=999)
    with pytest.raises(schemas.UnsupportedSchemaVersionError):
        parse_task(payload)


def test_supporting_fact_title_must_exist_in_context():
    payload = _factual_payload()
    payload["factual"]["supporting_facts"] = [
        {"title": "A", "sentence_index": 0},
        {"title": "MISSING", "sentence_index": 0},
    ]
    with pytest.raises(ValidationError, match="not in context"):
        parse_task(payload)


def test_supporting_fact_index_must_resolve():
    payload = _factual_payload()
    payload["factual"]["supporting_facts"] = [
        {"title": "A", "sentence_index": 0},
        {"title": "B", "sentence_index": 7},
    ]
    with pytest.raises(ValidationError, match="out of range"):
        parse_task(payload)


def test_supporting_facts_must_span_two_titles():
    payload = _factual_payload()
    payload["factual"]["supporting_facts"] = [
        {"title": "A", "sentence_index": 0},
        {"title": "A", "sentence_index": 1},
    ]
    with pytest.raises(ValidationError, match="two source titles"):
        parse_task(payload)


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        parse_task(_ethics_payload(unexpected_field="nope"))


def test_load_task_set_sorts_and_validates(tmp_path):
    first = _ethics_payload(id="ethics-002")
    first["ethics"]["source_row"] = 2
    second = _factual_payload()
    path = tmp_path / "task_set.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    records = load_task_set(path)
    assert [r.id for r in records] == ["ethics-002", "factual-001"]


def test_write_task_set_round_trip(tmp_path):
    record = parse_task(_factual_payload())
    path = tmp_path / "out.jsonl"
    write_task_set([record], path)
    loaded = load_task_set(path)
    assert len(loaded) == 1
    assert isinstance(loaded[0], FactualTask)
    assert loaded[0].factual.context[0].title == "A"


def test_serialized_record_uses_schema_envelope_keys():
    dumped = dump_task(parse_task(_ethics_payload()))
    assert dumped["schema"] == "task_record"
    assert "schema_id" not in dumped


def test_exported_json_schema_in_sync():
    """data/tasks/task_set.schema.json must match the Pydantic model."""
    from src.task_schema import TaskSetEnvelope

    path = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "data" / "tasks" / "task_set.schema.json"
    )
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert committed == TaskSetEnvelope.model_json_schema(), (
        "task_set.schema.json is stale; re-run scripts/export_task_schema.py"
    )
