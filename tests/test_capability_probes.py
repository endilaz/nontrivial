"""Offline integration test for the probe runner (scripts/capability_probes.py).

Uses a fake client (no network) to prove the paid-path wiring: reserve ->
run-directory + journal -> generate -> capability record -> reconcile. This is
the A3 (probe) x A4 (reservation) x A5 (append-only) x A7 (receipt) integration.
"""

import json
from datetime import date

from scripts.capability_probes import ProbeSpec, run_probe
from src.budget_state import approve_historical_spend, load_state
from src.generation import GenerationResult


class FakeClient:
    def __init__(self, result):
        self._result = result

    def generate(self, request):
        return self._result


def _result(reasoning_tokens=0, mechanism=None):
    return GenerationResult(
        text="Final answer: 42",
        provider="deepseek",
        model="deepseek-v4-flash",
        input_tokens=20,
        output_tokens=5,
        timestamp="2026-07-22T00:00:00+00:00",
        cache_hit=False,
        cache_key="c" * 64,
        request={},
        prefill_mechanism=mechanism,
        reasoning_tokens=reasoning_tokens,
        raw_response={"id": "resp_1", "usage": {"prompt_tokens": 20}},
        idempotency_key="cotf-abc",
    )


def _spec(assertion="reasoning_tokens_zero"):
    return ProbeSpec(
        probe_id="deepseek_nonthinking_zero_reasoning",
        provider="deepseek",
        model="deepseek-v4-flash",
        capability="thinking_disabled",
        assertion=assertion,
        messages=[{"role": "user", "content": "What is 40 + 2? Answer with just the number."}],
        max_tokens=32,
        expires_on="2026-08-21",
    )


def _approved_budget(tmp_path):
    p = tmp_path / "budget_state.json"
    approve_historical_spend(0.0, "test", path=p)
    return p


def test_probe_passes_and_reconciles(tmp_path):
    budget_path = _approved_budget(tmp_path)
    client = FakeClient(_result(reasoning_tokens=0))

    out = run_probe(
        _spec(), client,
        results_dir=tmp_path / "results",
        budget_path=budget_path,
        log_path=tmp_path / "cost_log.csv",
        today=date(2026, 7, 22),
    )

    assert out["summary"]["status"] == "pass"
    cap = out["capability"]
    assert cap.status == "pass"
    assert cap.receipt_sha256 is not None
    assert cap.expires_on == "2026-08-21"

    # Reservation was reconciled (none left dangling) and committed as v2 spend.
    state = load_state(budget_path)
    assert state.reservations == {}
    assert state.valid_v2_spend_usd >= 0.0

    # Run directory is append-only with a full state journal.
    run_dir = tmp_path / "results" / "probe_deepseek_nonthinking_zero_reasoning"
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "responses.jsonl").is_file()
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "raw" / "deepseek_nonthinking_zero_reasoning.json").is_file()
    journal = (run_dir / "journal.jsonl").read_text(encoding="utf-8")
    for state_name in ("planned", "submitted", "received", "persisted", "accounted"):
        assert state_name in journal


def test_probe_fail_status_when_reasoning_tokens_nonzero(tmp_path):
    budget_path = _approved_budget(tmp_path)
    client = FakeClient(_result(reasoning_tokens=7))
    out = run_probe(
        _spec(), client,
        results_dir=tmp_path / "results",
        budget_path=budget_path,
        log_path=tmp_path / "cost_log.csv",
        today=date(2026, 7, 22),
    )
    assert out["summary"]["status"] == "fail"
    # Even on a failed capability, the reservation is reconciled (not leaked).
    assert load_state(budget_path).reservations == {}


def test_probe_blocked_when_budget_unapproved(tmp_path):
    from src.budget_state import BudgetNotApprovedError

    unapproved = tmp_path / "budget_state.json"  # absent -> fail closed
    client = FakeClient(_result())
    try:
        run_probe(
            _spec(), client,
            results_dir=tmp_path / "results",
            budget_path=unapproved,
            log_path=tmp_path / "cost_log.csv",
            today=date(2026, 7, 22),
        )
        assert False, "expected BudgetNotApprovedError"
    except BudgetNotApprovedError:
        pass
