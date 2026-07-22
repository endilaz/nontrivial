"""Provider prompt-cache policy + cold/warm cost bounds (Milestone A4; risk E11).

Corruption variants share a problem stem, so stem-first prompts let providers
serve the shared prefix from prompt cache. That changes cost AND comparability:
two conditions must use the SAME prompt-cache policy or a cache-price difference
masquerades as a treatment effect. This module versions the policy and estimates
the cold (first, cache-write) and warm (subsequent, cache-read) cost bounds.

Token accounting uses the price table's per-MTok rates:
- cold  = full prompt at input rate + prefix written at cache-write rate
- warm  = prefix read at cached-input rate + remaining input at input rate
Output cost is identical across cold/warm and priced by the caller.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.costs import PRICES, ModelPrice, UnverifiedPriceError

PROMPT_CACHE_SCHEMA = "prompt_cache_policy"
PROMPT_CACHE_SCHEMA_VERSION = 1


@dataclass
class PromptCachePolicy:
    """Versioned prompt-cache policy shared by compared conditions."""

    policy_version: str
    shared_prefix_sha256: str
    ttl_seconds: int
    directives: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": PROMPT_CACHE_SCHEMA,
            "schema_version": PROMPT_CACHE_SCHEMA_VERSION,
            "policy_version": self.policy_version,
            "shared_prefix_sha256": self.shared_prefix_sha256,
            "ttl_seconds": self.ttl_seconds,
            "directives": self.directives,
        }


def shared_prefix_hash(prefix_text: str) -> str:
    """Stable hash identifying the shared prompt prefix across conditions."""
    return hashlib.sha256(prefix_text.encode("utf-8")).hexdigest()


@dataclass
class CacheCostBounds:
    cold_input_usd: float  # first call: full input + prefix cache-write
    warm_input_usd: float  # later calls: prefix cache-read + remaining input
    prefix_tokens: int
    input_tokens: int


def estimate_cache_bounds(
    model: str,
    input_tokens: int,
    prefix_tokens: int,
    prices: Optional[Dict[str, ModelPrice]] = None,
) -> CacheCostBounds:
    """Cold/warm INPUT-side cost bounds for a stem-first prompt.

    ``prefix_tokens`` is the cacheable shared prefix (<= ``input_tokens``). Raises
    :class:`UnverifiedPriceError` for an unverified model. If the model has no
    cache-write rate recorded, cold assumes no extra write surcharge (0) so the
    bound is not inflated by an invented number.
    """
    table = prices if prices is not None else PRICES
    price = table.get(model)
    if price is None or not price.is_verified:
        raise UnverifiedPriceError(f"verified price required to bound cache cost for {model!r}")
    prefix = max(0, min(prefix_tokens, input_tokens))
    remaining = input_tokens - prefix
    in_rate = price.input_per_mtok  # verified => not None
    read_rate = price.cached_input_per_mtok if price.cached_input_per_mtok is not None else in_rate
    write_rate = price.cache_write_per_mtok if price.cache_write_per_mtok is not None else 0.0

    cold = input_tokens / 1_000_000 * in_rate + prefix / 1_000_000 * write_rate  # type: ignore[operator]
    warm = prefix / 1_000_000 * read_rate + remaining / 1_000_000 * in_rate  # type: ignore[operator]
    return CacheCostBounds(
        cold_input_usd=cold,
        warm_input_usd=warm,
        prefix_tokens=prefix,
        input_tokens=input_tokens,
    )
