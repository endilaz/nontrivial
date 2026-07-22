"""Tests for the A7 raw-receipt redaction/allowlist/permissions (src/receipts.py)
and the semantic cache-envelope verification added to the generation client.
"""

import json
import stat

import pytest

from src.generation import GenerationClient, GenerationRequest, cache_key
from src.receipts import (
    REDACTION,
    redact_secrets,
    sanitize_receipt,
    write_receipt,
)


# --------------------------------------------------------------------------- #
# Receipt security
# --------------------------------------------------------------------------- #


def test_redaction_replaces_secret_shapes():
    blob = {
        "note": "call used sk-abcdEFGH1234567890 and Bearer abc123DEF456ghi789",
        "nested": ["AIzaSyD1234567890abcdefghijklmnopqrstu", "harmless"],
    }
    red = redact_secrets(blob)
    dumped = json.dumps(red)
    assert "sk-abcdEFGH1234567890" not in dumped
    assert "AIzaSyD1234567890" not in dumped
    assert REDACTION in red["note"]
    assert red["nested"][1] == "harmless"


def test_sanitize_drops_secret_named_and_non_allowlisted_keys():
    raw = {
        "id": "resp_1",
        "model": "gpt-5.4-mini",
        "authorization": "Bearer super-secret-token-abcdef123456",
        "api_key": "sk-should-not-survive-0123456789",
        "internal_debug": {"trace": "drop me"},
        "usage": {"prompt_tokens": 10},
    }
    clean = sanitize_receipt(raw)
    assert set(clean) == {"id", "model", "usage"}  # secret + non-allowlisted dropped
    assert "authorization" not in clean
    assert "internal_debug" not in clean


def test_write_receipt_is_mode_600(tmp_path):
    path = write_receipt(
        tmp_path / "raw" / "resp.json",
        {"id": "r1", "model": "m", "authorization": "Bearer abcdef1234567890"},
    )
    assert path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "authorization" not in on_disk


# --------------------------------------------------------------------------- #
# Semantic cache-envelope verification
# --------------------------------------------------------------------------- #


def _req(**overrides):
    base = dict(
        provider="openai",
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=128,
    )
    base.update(overrides)
    return GenerationRequest(**base)


def test_tampered_envelope_is_quarantined_not_served(tmp_path, monkeypatch):
    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)
    req = _req()
    key = cache_key(req)
    # A file at the right FILENAME but whose stored request envelope does not
    # hash to it (e.g. messages were altered after the fact).
    tampered_request = req.model_dump(exclude={"force_fresh"})
    tampered_request["messages"] = [{"role": "user", "content": "DIFFERENT"}]
    entry = {
        "text": "answer from a mismatched envelope",
        "provider": req.provider,
        "model": req.model,
        "timestamp": "2026-07-21T00:00:00+00:00",
        "cache_hit": False,
        "cache_key": key,  # claims this key...
        "cache_schema_version": 2,
        "request": tampered_request,  # ...but the envelope says otherwise
    }
    (tmp_path / f"{key}.json").write_text(json.dumps(entry), encoding="utf-8")

    calls = {"n": 0}

    def fake_invoke(request):
        calls["n"] += 1
        return {"text": "fresh", "input_tokens": 1, "output_tokens": 1, "raw_response": {}}

    monkeypatch.setattr(client, "_invoke_provider", fake_invoke)

    result = client.generate(req)
    assert result.cache_hit is False  # tampered entry not served
    assert calls["n"] == 1
    # And the bad entry was quarantined, not left in place.
    assert list((tmp_path / "quarantine").glob("*.envelope.json"))


def test_intact_envelope_still_round_trips(tmp_path, monkeypatch):
    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)

    def fake_invoke(request):
        return {"text": "ok", "input_tokens": 1, "output_tokens": 1, "raw_response": {}}

    monkeypatch.setattr(client, "_invoke_provider", fake_invoke)
    req = _req()
    client.generate(req)  # writes a well-formed entry
    hit = client.generate(req)  # envelope re-hash matches -> served
    assert hit.cache_hit is True
    assert hit.text == "ok"
