"""Tests for the A4 prompt-cache policy + cold/warm cost bounds."""

import pytest

from src.costs import UnverifiedPriceError
from src.prompt_cache import (
    PromptCachePolicy,
    estimate_cache_bounds,
    shared_prefix_hash,
)


def test_policy_serializes_with_schema_version():
    pol = PromptCachePolicy(
        policy_version="pc-v1",
        shared_prefix_sha256=shared_prefix_hash("shared stem"),
        ttl_seconds=300,
        directives={"cache_control": "ephemeral"},
    )
    d = pol.to_dict()
    assert d["schema_version"] == 1
    assert d["policy_version"] == "pc-v1"
    assert len(d["shared_prefix_sha256"]) == 64
    assert d["ttl_seconds"] == 300


def test_warm_is_cheaper_than_cold_for_cached_prefix():
    # deepseek-v4-flash: input 0.14/MTok, cached-input 0.0028/MTok.
    bounds = estimate_cache_bounds(
        "deepseek-v4-flash", input_tokens=10_000, prefix_tokens=8_000
    )
    # cold has no cache-write rate recorded -> just full input at 0.14.
    assert bounds.cold_input_usd == pytest.approx(10_000 / 1_000_000 * 0.14)
    # warm: 8000 at cached rate + 2000 at input rate.
    expected_warm = 8_000 / 1_000_000 * 0.0028 + 2_000 / 1_000_000 * 0.14
    assert bounds.warm_input_usd == pytest.approx(expected_warm)
    assert bounds.warm_input_usd < bounds.cold_input_usd


def test_prefix_capped_at_input_tokens():
    bounds = estimate_cache_bounds(
        "deepseek-v4-flash", input_tokens=1_000, prefix_tokens=5_000
    )
    assert bounds.prefix_tokens == 1_000  # capped


def test_unverified_model_cannot_be_bounded():
    with pytest.raises(UnverifiedPriceError):
        estimate_cache_bounds("deepseek-r1-distill-qwen-7b", input_tokens=100, prefix_tokens=50)
