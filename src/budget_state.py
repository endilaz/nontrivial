"""Budget state with atomic reservations (Milestone A4).

The hard $450 project budget cannot be derived from the repository cost log: the
2026-07-21 audit invalidated that ledger (duplicate cache-hit rows; missing judge
spend). Remaining budget is therefore tracked in a **locked** ``budget_state.json``:

    remaining = budget
              - historical_spend        (researcher-approved dashboard total)
              - valid_v2_spend          (committed spend from valid v2 runs)
              - sum(active reservations)

Fail-closed rule (plan.md §A4): **paid submissions are blocked** until the state
file records a researcher-approved ``historical_spend`` (or a conservative upper
bound). Until then, :func:`reserve` raises :class:`BudgetNotApprovedError`.

A reservation is taken *before* a paid batch, atomically, under an exclusive
lock. After the batch, :func:`reconcile` converts the reservation into committed
``valid_v2_spend`` using the ACTUAL receipts and releases any unused amount; an
abandoned batch is dropped with :func:`release`.

This module performs no network calls. The historical-spend figure itself is a
researcher decision — see ``logs/invalidated/2026-07-21/reconciliation.md``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from src.costs import PROJECT_BUDGET_USD, BudgetExceededError

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = REPO_ROOT / "logs" / "budget_state.json"
STATE_SCHEMA = "budget_state"
STATE_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class BudgetNotApprovedError(RuntimeError):
    """No researcher-approved historical spend recorded — paid calls are blocked."""


class BudgetStateLockError(RuntimeError):
    """Another process holds the budget-state lock."""


class ReservationError(RuntimeError):
    """An invalid reservation operation (unknown/duplicate reservation id)."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# State model
# --------------------------------------------------------------------------- #


@dataclass
class BudgetState:
    budget_usd: float = PROJECT_BUDGET_USD
    historical_spend_usd: Optional[float] = None
    historical_spend_approved: bool = False
    historical_spend_source: Optional[str] = None
    valid_v2_spend_usd: float = 0.0
    reservations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    schema: str = STATE_SCHEMA
    schema_version: int = STATE_SCHEMA_VERSION

    # -- derived ----------------------------------------------------------- #

    @property
    def active_reservations_usd(self) -> float:
        return sum(float(r["amount_usd"]) for r in self.reservations.values())

    def remaining(self) -> float:
        """Remaining budget. Treats an unapproved/None historical spend as the
        full budget consumed (0 remaining) so nothing can be reserved."""
        if not self.historical_spend_approved or self.historical_spend_usd is None:
            return 0.0
        return (
            self.budget_usd
            - self.historical_spend_usd
            - self.valid_v2_spend_usd
            - self.active_reservations_usd
        )

    # -- serialization ----------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "budget_usd": self.budget_usd,
            "historical_spend_usd": self.historical_spend_usd,
            "historical_spend_approved": self.historical_spend_approved,
            "historical_spend_source": self.historical_spend_source,
            "valid_v2_spend_usd": self.valid_v2_spend_usd,
            "reservations": self.reservations,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BudgetState":
        version = data.get("schema_version")
        if version is not None and version != STATE_SCHEMA_VERSION:
            # Unknown future versions fail closed (plan.md §A7).
            raise ValueError(
                f"budget_state schema_version {version} != {STATE_SCHEMA_VERSION}"
            )
        return cls(
            budget_usd=float(data.get("budget_usd", PROJECT_BUDGET_USD)),
            historical_spend_usd=data.get("historical_spend_usd"),
            historical_spend_approved=bool(data.get("historical_spend_approved", False)),
            historical_spend_source=data.get("historical_spend_source"),
            valid_v2_spend_usd=float(data.get("valid_v2_spend_usd", 0.0)),
            reservations=dict(data.get("reservations", {})),
        )


# --------------------------------------------------------------------------- #
# Locked read-modify-write
# --------------------------------------------------------------------------- #


class _StateLock:
    """Exclusive ``budget_state.json.lock`` via ``O_CREAT | O_EXCL``."""

    def __init__(self, state_path: Path) -> None:
        self._lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    def __enter__(self) -> "_StateLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise BudgetStateLockError(
                f"budget state is locked: {self._lock_path}"
            ) from exc
        os.close(fd)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError:  # pragma: no cover
            pass


def load_state(path: Union[str, Path] = DEFAULT_STATE_PATH) -> BudgetState:
    """Load state, or return a fail-closed default if the file is absent."""
    path = Path(path)
    if not path.exists():
        return BudgetState()
    return BudgetState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _save_state(path: Path, state: BudgetState) -> None:
    _atomic_write_json(path, state.to_dict())


# --------------------------------------------------------------------------- #
# Public operations (each takes the lock for the whole read-modify-write)
# --------------------------------------------------------------------------- #


def reserve(
    reservation_id: str,
    amount_usd: float,
    run_id: str,
    *,
    path: Union[str, Path] = DEFAULT_STATE_PATH,
    note: str = "",
) -> BudgetState:
    """Atomically reserve ``amount_usd`` for a run. Fails closed if not approved.

    Raises :class:`BudgetNotApprovedError` when no approved historical spend is
    recorded, and :class:`BudgetExceededError` when the reservation would push
    remaining budget below zero.
    """
    if amount_usd < 0:
        raise ReservationError("reservation amount must be non-negative")
    path = Path(path)
    with _StateLock(path):
        state = load_state(path)
        if not state.historical_spend_approved or state.historical_spend_usd is None:
            raise BudgetNotApprovedError(
                "No researcher-approved historical_spend in budget_state.json — "
                "paid calls are blocked. Fill it from the provider-dashboard "
                "reconciliation (logs/invalidated/2026-07-21/reconciliation.md)."
            )
        if reservation_id in state.reservations:
            raise ReservationError(f"reservation id already exists: {reservation_id}")
        if amount_usd > state.remaining():
            raise BudgetExceededError(
                f"reservation ${amount_usd:.2f} exceeds remaining budget "
                f"${state.remaining():.2f}."
            )
        state.reservations[reservation_id] = {
            "amount_usd": float(amount_usd),
            "run_id": run_id,
            "note": note,
            "created_at": _now_iso(),
        }
        _save_state(path, state)
        return state


def release(
    reservation_id: str, *, path: Union[str, Path] = DEFAULT_STATE_PATH
) -> BudgetState:
    """Drop an unused reservation (e.g. an abandoned batch), freeing its amount."""
    path = Path(path)
    with _StateLock(path):
        state = load_state(path)
        if reservation_id not in state.reservations:
            raise ReservationError(f"unknown reservation id: {reservation_id}")
        del state.reservations[reservation_id]
        _save_state(path, state)
        return state


def reconcile(
    reservation_id: str,
    actual_usd: float,
    *,
    path: Union[str, Path] = DEFAULT_STATE_PATH,
) -> BudgetState:
    """Convert a reservation to committed ``valid_v2_spend`` using actual receipts.

    Commits the ACTUAL spend (from final receipts, not the reserved upper bound)
    and releases the reservation, freeing any unused amount.
    """
    if actual_usd < 0:
        raise ReservationError("actual spend must be non-negative")
    path = Path(path)
    with _StateLock(path):
        state = load_state(path)
        if reservation_id not in state.reservations:
            raise ReservationError(f"unknown reservation id: {reservation_id}")
        del state.reservations[reservation_id]
        state.valid_v2_spend_usd += float(actual_usd)
        _save_state(path, state)
        return state


def approve_historical_spend(
    amount_usd: float,
    source: str,
    *,
    path: Union[str, Path] = DEFAULT_STATE_PATH,
) -> BudgetState:
    """Record the researcher-approved historical spend (unblocks paid calls).

    Intended to be called once, after provider-dashboard reconciliation. The
    ``source`` should cite where the figure came from.
    """
    if amount_usd < 0:
        raise ReservationError("historical spend must be non-negative")
    path = Path(path)
    with _StateLock(path):
        state = load_state(path)
        state.historical_spend_usd = float(amount_usd)
        state.historical_spend_approved = True
        state.historical_spend_source = source
        _save_state(path, state)
        return state
