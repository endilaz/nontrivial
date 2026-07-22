"""Tests for the A4 budget state + atomic reservations (src/budget_state.py)."""

import json
from pathlib import Path

import pytest

from src.budget_state import (
    DEFAULT_STATE_PATH,
    BudgetNotApprovedError,
    BudgetState,
    BudgetStateLockError,
    ReservationError,
    _StateLock,
    approve_historical_spend,
    load_state,
    reconcile,
    release,
    reserve,
)
from src.costs import BudgetExceededError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _approved(tmp_path, historical=100.0):
    path = tmp_path / "budget_state.json"
    approve_historical_spend(historical, "test-dashboard", path=path)
    return path


def test_fail_closed_when_not_approved(tmp_path):
    # A brand-new (absent) state file is fail-closed: remaining is 0 and no
    # reservation may be taken.
    path = tmp_path / "budget_state.json"
    state = load_state(path)
    assert state.historical_spend_approved is False
    assert state.remaining() == 0.0
    with pytest.raises(BudgetNotApprovedError):
        reserve("r1", 1.0, "run-1", path=path)


def test_approve_unblocks_and_sets_remaining(tmp_path):
    path = _approved(tmp_path, historical=50.0)
    state = load_state(path)
    assert state.historical_spend_approved is True
    assert state.remaining() == pytest.approx(450.0 - 50.0)


def test_reserve_reduces_remaining_and_persists(tmp_path):
    path = _approved(tmp_path, historical=0.0)
    reserve("r1", 30.0, "run-1", path=path)
    state = load_state(path)
    assert state.active_reservations_usd == pytest.approx(30.0)
    assert state.remaining() == pytest.approx(420.0)
    # Persisted to disk.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["reservations"]["r1"]["amount_usd"] == 30.0
    assert on_disk["reservations"]["r1"]["run_id"] == "run-1"


def test_reservation_exceeding_remaining_is_rejected_and_state_unchanged(tmp_path):
    path = _approved(tmp_path, historical=400.0)  # remaining 50
    reserve("r1", 40.0, "run-1", path=path)  # remaining now 10
    with pytest.raises(BudgetExceededError):
        reserve("r2", 20.0, "run-2", path=path)
    state = load_state(path)
    # r2 was NOT recorded; r1 intact.
    assert set(state.reservations) == {"r1"}
    assert state.remaining() == pytest.approx(10.0)


def test_release_frees_the_reservation(tmp_path):
    path = _approved(tmp_path, historical=0.0)
    reserve("r1", 100.0, "run-1", path=path)
    assert load_state(path).remaining() == pytest.approx(350.0)
    release("r1", path=path)
    assert load_state(path).remaining() == pytest.approx(450.0)
    assert load_state(path).reservations == {}


def test_reconcile_commits_actual_spend_and_frees_reservation(tmp_path):
    path = _approved(tmp_path, historical=0.0)
    reserve("r1", 100.0, "run-1", path=path)  # upper-bound reservation
    # Actual spend came in under the reservation.
    reconcile("r1", 62.5, path=path)
    state = load_state(path)
    assert state.reservations == {}
    assert state.valid_v2_spend_usd == pytest.approx(62.5)
    # Remaining reflects the committed actual, and the unused 37.5 is freed.
    assert state.remaining() == pytest.approx(450.0 - 62.5)


def test_duplicate_reservation_id_rejected(tmp_path):
    path = _approved(tmp_path, historical=0.0)
    reserve("r1", 10.0, "run-1", path=path)
    with pytest.raises(ReservationError):
        reserve("r1", 5.0, "run-1", path=path)


def test_release_unknown_reservation_raises(tmp_path):
    path = _approved(tmp_path, historical=0.0)
    with pytest.raises(ReservationError):
        release("nope", path=path)


def test_lock_blocks_second_holder(tmp_path):
    path = tmp_path / "budget_state.json"
    with _StateLock(path):
        with pytest.raises(BudgetStateLockError):
            with _StateLock(path):
                pass


def test_unknown_schema_version_fails_closed(tmp_path):
    path = tmp_path / "budget_state.json"
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_state(path)


def test_committed_budget_state_is_approved_with_provenance():
    # The researcher recorded the reconciled historical spend (2026-07-22), so
    # the committed state is now approved. It must cite its provenance and leave
    # a sane remaining budget (below the full $450, above zero).
    committed = json.loads(DEFAULT_STATE_PATH.read_text(encoding="utf-8"))
    assert committed["historical_spend_approved"] is True
    assert isinstance(committed["historical_spend_usd"], (int, float))
    assert committed["historical_spend_source"]  # non-empty provenance
    remaining = BudgetState.from_dict(committed).remaining()
    assert 0.0 < remaining <= 450.0
