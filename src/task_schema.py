"""Canonical Phase 1 task schema (Milestone B1).

One validated Pydantic model per task family, combined into a discriminated
union on ``task_type``. Common fields come from plan.md §B1; type-specific
metadata is typed, never an untyped dict.

Schema id: ``task_record``, version 1 (see ``src/schemas.py``). Every
serialized record carries ``schema``/``schema_version`` and unknown or future
versions fail closed at load time.

Generation/review split (plan.md §B1): candidate records are deterministic
artifacts produced by ``src/task_set.py``; human review decisions live in
separate immutable files keyed by task id. Canonical merging sorts by task id
and records the frozen review-file hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src import schemas

TASK_SCHEMA_VERSION = schemas.current_version("task_record")

TASK_TYPES = ("math", "logic", "factual", "ethics")

#: task ids look like ``math-001``; the prefix must match ``task_type``.
_ID_PATTERN = r"^(math|logic|factual|ethics)-\d{3}$"


# --------------------------------------------------------------------------- #
# Shared base
# --------------------------------------------------------------------------- #


class TaskBase(BaseModel):
    """Fields every task record carries (plan.md §B1)."""

    # Serialized field name "schema" follows the A7 registry envelope
    # convention; the Python attribute is renamed to avoid shadowing
    # BaseModel.schema. populate_by_name lets payloads use "schema".
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["task_record"] = Field(default="task_record", alias="schema")
    schema_version: int = TASK_SCHEMA_VERSION

    id: str = Field(pattern=_ID_PATTERN)
    task_type: Literal["math", "logic", "factual", "ethics"]
    problem: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_split: Literal["train", "validation", "test"]
    source_license: str = Field(min_length=1)
    ground_truth: str = Field(min_length=1)
    answer_format: Literal["number", "short_text", "yes_no"]
    scoring_policy: Literal["numeric", "normalized_text", "source_label_agreement"]

    binary_ok: bool
    direction2_probe: bool = False
    binary_formulation: Optional[str] = None
    binary_ground_truth: Optional[Literal["yes", "no"]] = None
    binary_claim_source: Optional[str] = None

    review_status: Literal["pending", "approved", "rejected"] = "pending"
    reviewer_notes: Optional[str] = None

    @field_validator("schema_version")
    @classmethod
    def _version_supported(cls, v: int) -> int:
        schemas.assert_supported("task_record", v)
        return v

    @model_validator(mode="after")
    def _check_binary_consistency(self) -> "TaskBase":
        if self.binary_ok:
            if not self.binary_formulation:
                raise ValueError("binary_ok tasks need binary_formulation")
            if self.binary_ground_truth not in ("yes", "no"):
                raise ValueError("binary_ok tasks need binary_ground_truth yes|no")
            if not self.binary_claim_source:
                raise ValueError("binary_ok tasks need binary_claim_source")
        if self.direction2_probe and not self.binary_ok:
            raise ValueError("direction2_probe implies binary_ok")
        if self.id.split("-")[0] != self.task_type:
            raise ValueError(f"id prefix must match task_type: {self.id}")
        return self


# --------------------------------------------------------------------------- #
# Type-specific metadata (discriminated union members)
# --------------------------------------------------------------------------- #


class MathTransformation(BaseModel):
    """One reviewed surface substitution applied to a math problem."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["name", "object", "wording"]
    original: str
    replacement: str


class MathMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_problem: str
    original_answer: str
    source_row: int
    transformations: List[MathTransformation]
    transformation_seed: int
    difficulty_proxy: Literal["solution_step_count"]
    difficulty_bin: int = Field(ge=0)


class MathTask(TaskBase):
    task_type: Literal["math"] = "math"
    answer_format: Literal["number"] = "number"
    scoring_policy: Literal["numeric"] = "numeric"
    math: MathMetadata


class LogicMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_example_index: int
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    solver_name: str
    solver_version: str
    unique_solution_proof: str


class LogicTask(TaskBase):
    task_type: Literal["logic"] = "logic"
    answer_format: Literal["short_text"] = "short_text"
    scoring_policy: Literal["normalized_text"] = "normalized_text"
    logic: LogicMetadata


class FactualContextEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    sentences: List[str]


class SupportingFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    sentence_index: int = Field(ge=0)


class FactualMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: List[FactualContextEntry]
    supporting_facts: List[SupportingFact] = Field(min_length=2)
    answer_aliases: List[str] = Field(default_factory=list)
    hotpot_type: Literal["bridge", "comparison"]
    hotpot_level: Literal["easy", "medium", "hard"]
    tokenizer_revision: str
    context_token_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_supporting_facts_resolve(self) -> "FactualMetadata":
        titles = {entry.title: len(entry.sentences) for entry in self.context}
        support_titles = set()
        for fact in self.supporting_facts:
            if fact.title not in titles:
                raise ValueError(f"supporting fact title not in context: {fact.title!r}")
            if fact.sentence_index >= titles[fact.title]:
                raise ValueError(
                    f"supporting fact index out of range: {fact.title!r}[{fact.sentence_index}]"
                )
            support_titles.add(fact.title)
        if len(support_titles) < 2:
            raise ValueError("evidence must span at least two source titles")
        return self


class FactualTask(TaskBase):
    task_type: Literal["factual"] = "factual"
    answer_format: Literal["short_text"] = "short_text"
    scoring_policy: Literal["normalized_text"] = "normalized_text"
    factual: FactualMetadata


class EthicsMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    raw_label: int = Field(ge=0, le=1)
    source_file: str
    source_row: int = Field(ge=0)
    label_mapping_version: str


class EthicsTask(TaskBase):
    task_type: Literal["ethics"] = "ethics"
    answer_format: Literal["yes_no"] = "yes_no"
    scoring_policy: Literal["source_label_agreement"] = "source_label_agreement"
    ethics: EthicsMetadata


#: Discriminated union over task_type.
TaskRecord = Union[MathTask, LogicTask, FactualTask, EthicsTask]


class TaskSetEnvelope(BaseModel):
    """Adapter used to parse/validate one JSONL line into the union."""

    model_config = ConfigDict(extra="forbid")
    record: TaskRecord = Field(discriminator="task_type")


# --------------------------------------------------------------------------- #
# Load / dump helpers
# --------------------------------------------------------------------------- #


def parse_task(data: Dict[str, Any]) -> TaskRecord:
    """Validate one raw record dict. Unknown/future schema versions fail closed."""
    schemas.check_payload("task_record", data)
    return TaskSetEnvelope.model_validate({"record": data}).record


def load_task_set(path: Union[str, Path]) -> List[TaskRecord]:
    """Load and validate a task-set JSONL file, sorted by task id."""
    records: List[TaskRecord] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(parse_task(json.loads(line)))
            except Exception as exc:  # fail loud with location
                raise ValueError(f"{path}:{line_number}: invalid task record: {exc}") from exc
    records.sort(key=lambda record: record.id)
    return records


def dump_task(record: TaskRecord) -> Dict[str, Any]:
    """Serialize a record to a plain dict for JSONL output."""
    return record.model_dump(mode="json", by_alias=True)


def write_task_set(records: List[TaskRecord], path: Union[str, Path]) -> None:
    """Write records sorted by task id, one JSON object per line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda record: record.id)
    with path.open("w", encoding="utf-8") as handle:
        for record in ordered:
            handle.write(json.dumps(dump_task(record), ensure_ascii=False) + "\n")
