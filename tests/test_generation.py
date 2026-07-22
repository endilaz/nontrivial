"""Tests for the generation client: cache key, response cache, retry/backoff.

No network: the provider call (``_invoke_provider``) is monkeypatched, and the
cache is redirected to a tmp dir. This is the component whose silent failures
would cause accidental double-spend or non-reproducible runs, so it is tested
hardest.
"""

import sys
from types import SimpleNamespace

import pytest

from src.generation import (
    PREFILL_INSTRUCTION,
    PREFILL_NATIVE,
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
        ("provider_route", "pinned-provider"),
        ("prompt_template_version", "v2"),
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


def test_v1_cache_record_cannot_satisfy_v2_request(tmp_path, monkeypatch):
    # A5/A7: a stale v1-schema cache entry must NOT be served to a v2 request.
    import json

    from src.generation import cache_key

    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)
    req = make_request()
    key = cache_key(req)
    # Hand-write a v1 cache file at the right filename but with the old schema.
    stale = {
        "text": "STALE v1 answer",
        "provider": req.provider,
        "model": req.model,
        "timestamp": "2026-07-13T00:00:00+00:00",
        "cache_hit": False,
        "cache_key": key,
        "cache_schema_version": 1,  # <-- old schema
        "request": {},
    }
    (tmp_path / f"{key}.json").write_text(json.dumps(stale), encoding="utf-8")

    calls = {"n": 0}

    def fake_invoke(request):
        calls["n"] += 1
        return stub_invocation(text="fresh v2 answer")

    monkeypatch.setattr(client, "_invoke_provider", fake_invoke)

    result = client.generate(req)
    assert result.cache_hit is False  # v1 entry ignored
    assert result.text == "fresh v2 answer"
    assert calls["n"] == 1


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
    assert len(list((tmp_path / "history").glob("*.json"))) == 1


def _install_fake_openai(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            usage = SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                prompt_tokens_details=SimpleNamespace(cached_tokens=3),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            )
            choice = SimpleNamespace(
                message=SimpleNamespace(content="Final answer: 42"),
                finish_reason="stop",
            )
            return SimpleNamespace(
                choices=[choice],
                usage=usage,
                system_fingerprint="fp_test",
                model_dump=lambda: {"ok": True},
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    return captured


def test_openai_adapter_applies_disabled_reasoning(monkeypatch, tmp_path):
    captured = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = GenerationClient(cache_dir=tmp_path, load_env=False)

    result = client._invoke_openai_compatible(make_request())

    assert captured["reasoning_effort"] == "none"
    assert "max_tokens" not in captured
    assert captured["max_completion_tokens"] == 256
    assert result["applied_settings"] == {"reasoning_effort": "none"}
    assert result["cached_input_tokens"] == 3


def test_openrouter_adapter_pins_route_and_thinking(monkeypatch, tmp_path):
    captured = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = GenerationClient(cache_dir=tmp_path, load_env=False)
    request = make_request(
        provider="openrouter",
        model="z-ai/glm-5.2",
        provider_route="DeepInfra",
        thinking_setting={"thinking": {"type": "disabled"}},
    )

    client._invoke_openai_compatible(request)

    assert captured["extra_body"]["thinking"] == {"type": "disabled"}
    assert captured["extra_body"]["provider"] == {
        "order": ["DeepInfra"],
        "allow_fallbacks": False,
    }


def test_openrouter_adapter_rejects_unpinned_route(monkeypatch, tmp_path):
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = GenerationClient(cache_dir=tmp_path, load_env=False)
    request = make_request(
        provider="openrouter",
        model="z-ai/glm-5.2",
        thinking_setting={"thinking": {"type": "disabled"}},
    )

    with pytest.raises(ValueError, match="pinned provider_route"):
        client._invoke_openai_compatible(request)


def test_deepseek_adapter_rejects_unapplied_string_setting(monkeypatch, tmp_path):
    _install_fake_openai(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = GenerationClient(cache_dir=tmp_path, load_env=False)
    request = make_request(
        provider="deepseek",
        model="deepseek-v4-flash",
        thinking_setting="disabled",
    )

    with pytest.raises(ValueError, match="provider-specific dict"):
        client._invoke_openai_compatible(request)


# --------------------------------------------------------------------------- #
# Prefill mechanism resolution (capability matrix, 2026-07-08)
# --------------------------------------------------------------------------- #


def test_prefill_mode_deepseek_native_others_instruction_only(tmp_path):
    # Researcher decision 2026-07-08: instruction-only is the primary mechanism
    # everywhere; native prefill only where the provider supports it (DeepSeek).
    client = GenerationClient(cache_dir=tmp_path, load_env=False, sleep_fn=lambda s: None)
    assert client._prefill_mode("deepseek", True) == PREFILL_NATIVE
    for provider in ("openai", "anthropic", "google", "openrouter", "together"):
        assert client._prefill_mode(provider, True) == PREFILL_INSTRUCTION
    assert client._prefill_mode("deepseek", False) is None  # no prefill requested


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


class FakeRateLimitWithRetryAfter(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("rate limited")
        self.response = SimpleNamespace(headers={"retry-after": "7"})


def test_idempotency_key_is_stable_and_derived_from_cache_key():
    from src.generation import idempotency_key

    req = make_request()
    assert idempotency_key(req) == f"cotf-{cache_key(req)}"
    assert idempotency_key(req) == idempotency_key(make_request())  # stable


def test_openai_adapter_forwards_timeout_and_idempotency_header(monkeypatch, tmp_path):
    from src.generation import idempotency_key

    captured = _install_fake_openai(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = GenerationClient(cache_dir=tmp_path, load_env=False)
    req = make_request()

    client._invoke_openai_compatible(req)

    assert captured["timeout"] == 120.0
    assert captured["extra_headers"]["Idempotency-Key"] == idempotency_key(req)


def test_retry_after_header_is_honored_over_backoff(tmp_path, monkeypatch):
    sleeps = []
    client = GenerationClient(
        cache_dir=tmp_path, load_env=False, max_retries=3, base_delay=0.01,
        sleep_fn=lambda s: sleeps.append(s),
    )
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise FakeRateLimitWithRetryAfter()
        return stub_invocation()

    monkeypatch.setattr(client, "_invoke_provider", flaky)
    client.generate(make_request())
    assert sleeps == [7.0]  # used Retry-After, not exponential backoff


def test_attempts_and_idempotency_recorded_on_result(tmp_path, monkeypatch):
    from src.generation import idempotency_key

    client = GenerationClient(
        cache_dir=tmp_path, load_env=False, max_retries=3, base_delay=0.01,
        sleep_fn=lambda s: None,
    )
    attempts = {"n": 0}

    def flaky(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise FakeRateLimit("rate limited")
        return stub_invocation()

    monkeypatch.setattr(client, "_invoke_provider", flaky)
    req = make_request()
    result = client.generate(req)
    assert len(result.attempts) == 2  # one failed, one succeeded
    assert result.idempotency_key == idempotency_key(req)


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
