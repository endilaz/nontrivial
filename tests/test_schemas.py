"""Tests for the A7 schema registry + migration policy (src/schemas.py)."""

import json

import pytest

from src import schemas
from src.schemas import (
    UnknownSchemaError,
    UnsupportedSchemaVersionError,
    assert_supported,
    check_payload,
    current_version,
    migrate_file,
    stamp,
)


def test_current_version_and_supported():
    assert current_version("cache_entry") == 2
    assert schemas.is_supported("run_manifest", 1) is True
    assert schemas.is_supported("run_manifest", 2) is False


def test_unknown_type_and_future_version_fail_closed():
    with pytest.raises(UnknownSchemaError):
        assert_supported("not_a_thing", 1)
    with pytest.raises(UnsupportedSchemaVersionError):
        assert_supported("cache_entry", 3)  # future
    with pytest.raises(UnsupportedSchemaVersionError):
        assert_supported("cache_entry", 1)  # older
    with pytest.raises(UnsupportedSchemaVersionError):
        check_payload("budget_state", {})  # missing schema_version -> None


def test_stamp_sets_schema_and_version():
    out = stamp("task_record", {"id": "math-001"})
    assert out["schema"] == "task_record"
    assert out["schema_version"] == 1
    assert out["id"] == "math-001"


def test_registry_agrees_with_module_constants():
    # Single source of truth: the registry must match each module's own constant.
    from src.budget_state import STATE_SCHEMA_VERSION
    from src.capabilities import CAPABILITY_SCHEMA_VERSION
    from src.corruption import CORRUPTION_RECORD_SCHEMA_VERSION
    from src.generation import CACHE_SCHEMA_VERSION
    from src.invalidation import MANIFEST_SCHEMA_VERSION as INVAL_V
    from src.run_manifest import MANIFEST_SCHEMA_VERSION as RUN_V

    assert current_version("cache_entry") == CACHE_SCHEMA_VERSION
    assert current_version("generation_response") == CACHE_SCHEMA_VERSION
    assert current_version("run_manifest") == RUN_V
    assert current_version("invalidation_manifest") == INVAL_V
    assert current_version("budget_state") == STATE_SCHEMA_VERSION
    assert current_version("capability_probe") == CAPABILITY_SCHEMA_VERSION
    assert current_version("corruption_record") == CORRUPTION_RECORD_SCHEMA_VERSION


def test_migrate_creates_new_file_records_source_hash_never_mutates(tmp_path):
    src = tmp_path / "old.json"
    src.write_text(json.dumps({"schema_version": 1, "value": 1}), encoding="utf-8")
    src_bytes_before = src.read_bytes()
    dest = tmp_path / "new.json"

    out = migrate_file(
        src, dest, "task_record",
        transform=lambda d: {"value": d["value"] + 100},
        migrated_on="2026-07-21",
    )
    # New file written, stamped, with provenance; source untouched.
    assert dest.is_file()
    assert out["value"] == 101
    assert out["schema_version"] == 1
    assert out["_migration"]["source_sha256"]
    assert out["_migration"]["migrated_on"] == "2026-07-21"
    assert src.read_bytes() == src_bytes_before

    # Refuses to overwrite an existing destination.
    with pytest.raises(FileExistsError):
        migrate_file(src, dest, "task_record", transform=lambda d: d, migrated_on="2026-07-21")
