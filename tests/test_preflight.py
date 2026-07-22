"""Tests for the A3 rendered-request token preflight (src/preflight.py)."""

import pytest

from src.generation import GenerationRequest
from src.preflight import (
    CONTEXT_WINDOWS,
    ContextWindow,
    ContextWindowExceededError,
    UnknownContextWindowError,
    preflight_request,
)

# A verified test window table (production CONTEXT_WINDOWS is UNVERIFIED on purpose).
_WINDOWS = {
    "gpt-5.4-mini": ContextWindow(total_tokens=1000, status="verified", source="test"),
}


def _req(**overrides):
    base = dict(
        provider="openai",
        model="gpt-5.4-mini",
        messages=[{"role": "user", "content": "hello world"}],
        max_tokens=256,
    )
    base.update(overrides)
    return GenerationRequest(**base)


def test_preflight_passes_when_prompt_plus_reserve_fits():
    result = preflight_request(_req(), windows=_WINDOWS, reserved_output_tokens=100)
    assert result.fits is True
    assert result.context_window == 1000
    assert result.reserved_output_tokens == 100
    assert result.headroom_tokens == 1000 - result.input_tokens - 100
    assert result.token_method  # records how tokens were counted


def test_preflight_rejects_oversize_before_spend():
    # Reserve nearly the whole window so prompt + reserve overflows.
    with pytest.raises(ContextWindowExceededError):
        preflight_request(_req(), windows=_WINDOWS, reserved_output_tokens=999)


def test_preflight_defaults_reserve_to_max_tokens():
    # max_tokens=1200 alone already exceeds the 1000-token window.
    with pytest.raises(ContextWindowExceededError):
        preflight_request(_req(max_tokens=1200), windows=_WINDOWS)


def test_unverified_or_unknown_model_fails_closed():
    # deepseek-v4-flash is present but UNVERIFIED in the production table.
    with pytest.raises(UnknownContextWindowError):
        preflight_request(_req(provider="deepseek", model="deepseek-v4-flash"))
    # A model absent from the table also fails closed.
    with pytest.raises(UnknownContextWindowError):
        preflight_request(_req(model="totally-unknown"), windows=_WINDOWS)


def test_production_windows_start_unverified():
    # Guard the honesty discipline: no invented windows ship enabled.
    assert all(not w.is_verified for w in CONTEXT_WINDOWS.values())
