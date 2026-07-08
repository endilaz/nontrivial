"""Tests for cost math, the UNVERIFIED-price guard, logging, and the budget/gate.

Cost bugs silently corrupt every spend decision, so the math and the
"never invent a price" guarantee are tested directly.
"""

import csv

import pytest

from src import costs
from src.costs import (
    BatchEstimate,
    ConfirmationRequired,
    UnverifiedPriceError,
    check_budget,
    compute_estimate,
    cost_for_usage,
    estimate_batch,
    log_cost,
)


# --------------------------------------------------------------------------- #
# Cost math
# --------------------------------------------------------------------------- #


def test_cost_for_usage_math_haiku():
    # claude-haiku-4-5 verified rates: $1.00 in / $5.00 out per MTok.
    # 2000 input + 500 output tokens.
    cost = cost_for_usage("claude-haiku-4-5", input_tokens=2000, output_tokens=500)
    expected = 2000 / 1_000_000 * 1.00 + 500 / 1_000_000 * 5.00
    assert cost == pytest.approx(expected)
    assert cost == pytest.approx(0.0045)


def test_cost_for_usage_at_one_million_tokens():
    cost = cost_for_usage("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(1.00 + 5.00)


def test_cost_for_usage_math_deepseek_v4_flash():
    # Recommended workhorse rates: $0.14 in (cache miss) / $0.28 out per MTok.
    cost = cost_for_usage(
        "deepseek-v4-flash", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert cost == pytest.approx(0.14 + 0.28)


def test_cost_for_usage_cached_input_uses_cached_rate():
    # 1000 input tokens, all cached: billed at the $0.10/MTok cached rate.
    cost = cost_for_usage(
        "claude-haiku-4-5", input_tokens=1000, output_tokens=0, cached_input_tokens=1000
    )
    assert cost == pytest.approx(1000 / 1_000_000 * 0.10)


# --------------------------------------------------------------------------- #
# UNVERIFIED-price guard (never invent a price)
# --------------------------------------------------------------------------- #


def test_unverified_model_raises():
    # The bridge model runs locally (no per-token price) — permanently UNVERIFIED
    # in the API price table.
    with pytest.raises(UnverifiedPriceError):
        cost_for_usage("deepseek-r1-distill-qwen-7b", input_tokens=100, output_tokens=100)


def test_unknown_model_raises():
    with pytest.raises(UnverifiedPriceError):
        cost_for_usage("totally-made-up-model", input_tokens=100, output_tokens=100)


def test_estimate_batch_raises_on_unverified_model():
    reqs = [{"model": "deepseek-r1-distill-qwen-7b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}]
    with pytest.raises(UnverifiedPriceError):
        estimate_batch(reqs, verbose=False)


# --------------------------------------------------------------------------- #
# Batch estimate + confirmation gate
# --------------------------------------------------------------------------- #


def _cheap_reqs(n=1):
    return [
        {
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "short prompt"}],
            "max_tokens": 16,
        }
        for _ in range(n)
    ]


def test_compute_estimate_returns_batchestimate():
    est = compute_estimate(_cheap_reqs(3))
    assert isinstance(est, BatchEstimate)
    assert est.n_requests == 3
    assert est.total_usd > 0
    assert "claude-haiku-4-5" in est.per_model


def test_estimate_batch_gate_blocks_over_threshold(capsys):
    # Force the gate by setting the threshold to 0 so any positive cost trips it.
    with pytest.raises(ConfirmationRequired):
        estimate_batch(_cheap_reqs(1), threshold_usd=0.0, verbose=False)


def test_estimate_batch_gate_passes_when_confirmed():
    est = estimate_batch(_cheap_reqs(1), threshold_usd=0.0, confirmed=True, verbose=False)
    assert est.n_requests == 1


def test_estimate_batch_under_threshold_no_gate():
    # Tiny batch well under $5 should not require confirmation.
    est = estimate_batch(_cheap_reqs(1), verbose=False)
    assert est.total_usd < costs.CONFIRM_THRESHOLD_USD


# --------------------------------------------------------------------------- #
# Cost logging
# --------------------------------------------------------------------------- #


def test_log_cost_creates_header_and_appends(tmp_path):
    log_path = tmp_path / "cost_log.csv"

    cum1 = log_cost(
        run_id="r1", model="claude-haiku-4-5", provider="anthropic",
        input_tokens=100, output_tokens=50, cost_usd=0.5,
        log_path=log_path, emit=False,
    )
    cum2 = log_cost(
        run_id="r2", model="claude-haiku-4-5", provider="anthropic",
        input_tokens=200, output_tokens=80, cost_usd=1.0,
        log_path=log_path, emit=False,
    )

    assert cum1 == pytest.approx(0.5)
    assert cum2 == pytest.approx(1.5)  # cumulative across rows

    with log_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert rows[0]["run_id"] == "r1"
    assert rows[1]["run_id"] == "r2"
    assert float(rows[1]["cumulative_project_cost"]) == pytest.approx(1.5)
    # header present with the exact expected columns
    with log_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == costs.CSV_HEADER


# --------------------------------------------------------------------------- #
# Budget guard
# --------------------------------------------------------------------------- #


def test_budget_warning_on_crossing_50pct():
    warnings = check_budget(cumulative_before=200.0, cumulative_after=230.0, budget=450.0)
    assert warnings and "50%" in warnings[0]


def test_budget_warning_on_crossing_80pct():
    warnings = check_budget(cumulative_before=350.0, cumulative_after=365.0, budget=450.0)
    assert warnings and "80%" in warnings[0]


def test_no_budget_warning_when_not_crossing():
    assert check_budget(cumulative_before=10.0, cumulative_after=20.0, budget=450.0) == []
