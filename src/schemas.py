"""Central artifact schema registry (Milestone A7).

Every persisted artifact type carries an explicit schema id + version, and the
registry below is the single source of truth for the CURRENT version of each.
The core rule (plan.md §A7): **unknown or future versions fail closed** —
loaders raise rather than silently mixing schemas. Migrations always create NEW
files and record the source hash; they never mutate a source artifact in place.

Other modules keep their own version constants (e.g.
``generation.CACHE_SCHEMA_VERSION``); a cross-check test asserts those agree with
this registry so the two never drift.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Union

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

#: artifact type -> current schema version. Fail closed for anything not equal
#: to the registered current version (older or newer).
SCHEMA_VERSIONS: Dict[str, int] = {
    "run_config": 1,
    "generation_request": 1,
    "generation_response": 2,  # == generation.CACHE_SCHEMA_VERSION
    "cache_entry": 2,  # == generation.CACHE_SCHEMA_VERSION
    "task_record": 1,
    "judgment": 1,
    "cost_row": 1,
    "run_manifest": 1,  # == run_manifest.MANIFEST_SCHEMA_VERSION
    "invalidation_manifest": 1,  # == invalidation.MANIFEST_SCHEMA_VERSION
    "budget_state": 1,  # == budget_state.STATE_SCHEMA_VERSION
    "capability_probe": 1,  # == capabilities.CAPABILITY_SCHEMA_VERSION
    "corruption_record": 1,  # == corruption.CORRUPTION_RECORD_SCHEMA_VERSION
    "analysis_table": 1,
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class UnknownSchemaError(KeyError):
    """The artifact type is not in the registry."""


class UnsupportedSchemaVersionError(RuntimeError):
    """The artifact's schema version is not the supported current version."""


# --------------------------------------------------------------------------- #
# Version checks
# --------------------------------------------------------------------------- #


def current_version(artifact_type: str) -> int:
    if artifact_type not in SCHEMA_VERSIONS:
        raise UnknownSchemaError(f"no registered schema for {artifact_type!r}")
    return SCHEMA_VERSIONS[artifact_type]


def is_supported(artifact_type: str, version: Any) -> bool:
    return artifact_type in SCHEMA_VERSIONS and version == SCHEMA_VERSIONS[artifact_type]


def assert_supported(artifact_type: str, version: Any) -> None:
    """Raise unless ``version`` is exactly the registered current version.

    Fail-closed: an unknown type, a missing version, or any older/newer version
    raises. Reading an old version is a migration's job, not a loader's.
    """
    if artifact_type not in SCHEMA_VERSIONS:
        raise UnknownSchemaError(f"no registered schema for {artifact_type!r}")
    expected = SCHEMA_VERSIONS[artifact_type]
    if version != expected:
        raise UnsupportedSchemaVersionError(
            f"{artifact_type}: schema version {version!r} is not supported "
            f"(expected {expected}). Unknown/future versions fail closed; "
            f"migrate to a new file rather than reading in place."
        )


def stamp(artifact_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``data`` with ``schema``/``schema_version`` set to the current version."""
    out = dict(data)
    out["schema"] = artifact_type
    out["schema_version"] = current_version(artifact_type)
    return out


def check_payload(artifact_type: str, data: Dict[str, Any]) -> None:
    """Assert a loaded payload carries a supported ``schema_version``."""
    assert_supported(artifact_type, data.get("schema_version"))


# --------------------------------------------------------------------------- #
# Migration policy (new file only; record source hash; never mutate source)
# --------------------------------------------------------------------------- #


def sha256_file(path: Union[str, Path]) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def migrate_file(
    source_path: Union[str, Path],
    dest_path: Union[str, Path],
    artifact_type: str,
    transform: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    migrated_on: str,
) -> Dict[str, Any]:
    """Migrate a JSON artifact to a NEW file, recording the source hash.

    Never mutates or deletes the source. Refuses to overwrite an existing
    destination. ``transform`` maps the old payload to a new-version payload;
    the result is stamped to the current version and annotated with a
    ``_migration`` provenance block. ``migrated_on`` is supplied by the caller
    (this module does not read the clock, keeping it deterministic/testable).
    """
    source_path = Path(source_path)
    dest_path = Path(dest_path)
    if dest_path.exists():
        raise FileExistsError(f"migration destination already exists: {dest_path}")

    src_hash = sha256_file(source_path)
    old = json.loads(source_path.read_text(encoding="utf-8"))
    new = stamp(artifact_type, transform(old))
    new["_migration"] = {
        "source_path": str(source_path),
        "source_sha256": src_hash,
        "migrated_on": migrated_on,
        "to_version": current_version(artifact_type),
    }
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    return new
