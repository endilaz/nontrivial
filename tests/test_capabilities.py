"""Tests for the A3 capability-probe records with expiration (src/capabilities.py)."""

import json
from datetime import date

import pytest

from src.capabilities import (
    CapabilityError,
    CapabilityProbe,
    load_matrix,
    matrix_to_dict,
    require_valid,
)

TODAY = date(2026, 7, 21)


def _probe(**overrides):
    base = dict(
        probe_id="deepseek_thinking_disabled",
        provider="deepseek",
        model="deepseek-v4-flash",
        capability="thinking_disabled",
        status="pass",
        verified_on="2026-07-20",
        expires_on="2026-08-20",
        receipt_sha256="a" * 64,
    )
    base.update(overrides)
    return CapabilityProbe(**base)


def test_passing_unexpired_probe_is_usable():
    probe = _probe()
    assert probe.is_usable(TODAY) is True
    assert require_valid(probe, TODAY) is probe


def test_expired_pass_requires_revalidation():
    probe = _probe(expires_on="2026-07-01")  # already expired on TODAY
    assert probe.is_expired(TODAY) is True
    assert probe.is_usable(TODAY) is False
    with pytest.raises(CapabilityError, match="expired"):
        require_valid(probe, TODAY)


def test_fail_or_inconclusive_cannot_be_relied_on():
    for status in ("fail", "inconclusive"):
        with pytest.raises(CapabilityError):
            require_valid(_probe(status=status), TODAY)


def test_missing_receipt_fails_closed():
    with pytest.raises(CapabilityError):
        require_valid(_probe(receipt_sha256=None), TODAY)


def test_missing_probe_fails_closed():
    with pytest.raises(CapabilityError):
        require_valid(None, TODAY)


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        _probe(status="maybe")


def test_matrix_roundtrip_and_schema_gate(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(matrix_to_dict([_probe()])), encoding="utf-8")
    loaded = load_matrix(path)
    assert loaded["deepseek_thinking_disabled"].capability == "thinking_disabled"

    # Unknown schema version fails closed.
    path.write_text(json.dumps({"schema_version": 999, "probes": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_matrix(path)
