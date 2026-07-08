"""Tests for the generation client: cache key, response cache, retry/backoff.

No network: the provider call (``_invoke_provider``) is monkeypatched, and the
cache is redirected to a tmp dir. This is the component whose silent failures
would cause accidental double-spend or non-reproducible runs, so it is tested
hardest.
"""

import pytest

from src.generation import (
    GenerationClient,
    GenerationRequest,
    cache_key,
)


def make_request(**overrides):
    base = dict(
        provider="openai",
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        temperature=0.0,
        max_tokens=256,
        seed=0,
        thinking_setting="disabled",
        replicate_index=0,
    )
    base.update(overrides)
    return GenerationRequest(**base)


def stub_invocation(text="Final answer: 42"):
    """A normalized provider return, as _invoke_provider would produce."""
    return {
        "text": text,
        "input_tokens": 10,
        "output_tokens": 5,
        "prefill_mechanism": None,
        "system_fingerprint": "fp_test",
        "raw_response": {"ok": True},
    }


# --------------------------------------------------------------------------- #
# Cache key
# --------------------------------------------------------------------------- #


def test_cache_key_is_stable_across_identical_requests():
    assert cache_key(make_request()) == cache_key(make_request())


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "anthropic"),
        ("model", "claude-haiku-4-5"),
        ("temperature", 0.7),
        ("max_tokens", 1024),
        ("seed", 99),
        ("thinking_setting", {"type": "adaptive"}),
        ("replicate_index", 1),
        ("messages", [{"role": "user", "content": "different"}]),
    ],
)
def test_cache_key_changes_when_any_key_component_changes(field, value):
    base_key = cache_key(make_request())
    assert cache_key(make_request(**{field: value})) != base_key


def test_replicate_index_differentiates_otherwise_identical_requests():
    # This is exactly what the Phase 3 nondeterminism control relies on.
    k0 = cache_key(make_request(replicate_index=0))
    k1 = cache_key(make_request(replicate_index=1))
    assert k0 != k1


def test_force_fresh_does_not_change_cache_key():
    # force_fresh is a control flag, not a request parameter.
    assert cache_key(make_request(force_fresh=False)) == cache_key(
        make_request(force_fresh=True)
    )


# --------------------------------------------------------------------------- #
# Response cache round-trip
# --------------------------------------------------------------------------- #


def test_cache_miss_then_hit_roundtrip(tmp_path, monkeypatch):
    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)
    calls = {"n": 0}

    def fake_invoke(request):
        calls["n"] += 1
        return stub_invocation()

    monkeypatch.setattr(client, "_invoke_provider", fake_invoke)

    req = make_request()
    first = client.generate(req)
    assert first.cache_hit is False
    assert first.text == "Final answer: 42"
    assert calls["n"] == 1

    second = client.generate(req)
    assert second.cache_hit is True  # served from disk
    assert second.text == first.text
    assert second.cache_key == first.cache_key
    assert calls["n"] == 1  # provider NOT called again


def test_force_fresh_bypasses_read_but_still_writes(tmp_path, monkeypatch):
    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)
    calls = {"n": 0}

    def fake_invoke(request):
        calls["n"] += 1
        return stub_invocation(text=f"Final answer: {calls['n']}")

    monkeypatch.setattr(client, "_invoke_provider", fake_invoke)

    client.generate(make_request())  # populate cache
    assert calls["n"] == 1

    fresh = client.generate(make_request(force_fresh=True))  # bypass read
    assert fresh.cache_hit is False
    assert calls["n"] == 2  # provider called again

    # ...and the fresh call still wrote through: a normal read now hits.
    hit = client.generate(make_request())
    assert hit.cache_hit is True
    assert hit.text == fresh.text  # force_fresh overwrote the cached entry
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# Retry / backoff
# --------------------------------------------------------------------------- #


class FakeRateLimit(Exception):
    status_code = 429


class FakeBadRequest(Exception):
    status_code = 400


def test_retry_triggers_on_429_then_succeeds(tmp_path, monkeypatch):
    sleeps = []
    client = GenerationClient(
        cache_dir=tmp_path,
        load_env=False,
        max_retries=3,
        base_delay=0.01,
        sleep_fn=lambda s: sleeps.append(s),
    )
    attempts = {"n": 0}

    def flaky_invoke(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise FakeRateLimit("rate limited")
        return stub_invocation()

    monkeypatch.setattr(client, "_invoke_provider", flaky_invoke)

    result = client.generate(make_request())
    assert result.text == "Final answer: 42"
    assert attempts["n"] == 2  # failed once, then succeeded
    assert len(sleeps) == 1  # backed off exactly once


def test_non_retryable_error_raises_immediately(tmp_path, monkeypatch):
    client = GenerationClient(
        cache_dir=tmp_path, load_env=False, max_retries=3, sleep_fn=lambda s: None
    )
    attempts = {"n": 0}

    def bad_invoke(request):
        attempts["n"] += 1
        raise FakeBadRequest("bad request")

    monkeypatch.setattr(client, "_invoke_provider", bad_invoke)

    with pytest.raises(FakeBadRequest):
        client.generate(make_request())
    assert attempts["n"] == 1  # a 400 is not retried


def test_retry_gives_up_after_max_retries(tmp_path, monkeypatch):
    client = GenerationClient(
        cache_dir=tmp_path,
        load_env=False,
        max_retries=2,
        base_delay=0.01,
        sleep_fn=lambda s: None,
    )
    attempts = {"n": 0}

    def always_429(request):
        attempts["n"] += 1
        raise FakeRateLimit("still rate limited")

    monkeypatch.setattr(client, "_invoke_provider", always_429)

    with pytest.raises(FakeRateLimit):
        client.generate(make_request())
    assert attempts["n"] == 3  # 1 initial + 2 retries
