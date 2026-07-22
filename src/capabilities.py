"""Capability-probe records with expiration (Milestone A3).

Each live capability probe (e.g. "does DeepSeek non-thinking return zero
reasoning tokens?", "does native prefill continue the assistant prefix?") is
recorded as a ``pass`` / ``fail`` / ``inconclusive`` result with the raw receipt
hash and an expiration/revalidation date. Canonical runs must consult these:

- A ``fail`` or ``inconclusive`` capability may NOT be relied on — it changes the
  capability matrix / model stratum, it is not patched over in analysis.
- An expired ``pass`` must be re-probed before canonical use (models/endpoints
  drift; a stale pass is not evidence about today's endpoint).

This module is pure bookkeeping and does not run probes (those are live/gated).
It fails closed: unknown status, missing verification, or an expired/absent
record all make :func:`require_valid` raise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

CAPABILITY_SCHEMA = "capability_probe"
CAPABILITY_SCHEMA_VERSION = 1

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_INCONCLUSIVE = "inconclusive"
_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_INCONCLUSIVE)


class CapabilityError(RuntimeError):
    """A required capability is missing, failed, inconclusive, or expired."""


@dataclass
class CapabilityProbe:
    probe_id: str
    provider: str
    model: str
    capability: str  # e.g. "thinking_disabled", "native_prefill"
    status: str  # pass | fail | inconclusive
    verified_on: str  # ISO date the probe was run
    expires_on: str  # ISO date after which it must be re-run
    receipt_sha256: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"status {self.status!r} not in {_STATUSES}")

    def is_expired(self, today: date) -> bool:
        return today > date.fromisoformat(self.expires_on)

    def is_usable(self, today: date) -> bool:
        """True only for a passing, receipt-backed, unexpired probe."""
        return (
            self.status == STATUS_PASS
            and self.receipt_sha256 is not None
            and not self.is_expired(today)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "provider": self.provider,
            "model": self.model,
            "capability": self.capability,
            "status": self.status,
            "verified_on": self.verified_on,
            "expires_on": self.expires_on,
            "receipt_sha256": self.receipt_sha256,
            "notes": self.notes,
        }


def require_valid(probe: Optional[CapabilityProbe], today: date) -> CapabilityProbe:
    """Return the probe if it is usable today; otherwise raise (fail closed)."""
    if probe is None:
        raise CapabilityError("no capability probe recorded for this requirement")
    if probe.status != STATUS_PASS:
        raise CapabilityError(
            f"capability {probe.capability!r} for {probe.model!r} is "
            f"{probe.status!r}; it may not be relied on for canonical runs"
        )
    if probe.receipt_sha256 is None:
        raise CapabilityError(
            f"capability {probe.capability!r} for {probe.model!r} has no receipt hash"
        )
    if probe.is_expired(today):
        raise CapabilityError(
            f"capability {probe.capability!r} for {probe.model!r} expired on "
            f"{probe.expires_on}; re-run the probe before canonical use"
        )
    return probe


# --------------------------------------------------------------------------- #
# Matrix persistence
# --------------------------------------------------------------------------- #


def load_matrix(path: Union[str, Path]) -> Dict[str, CapabilityProbe]:
    """Load a capability matrix keyed by ``probe_id``. Fails closed on schema drift."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != CAPABILITY_SCHEMA_VERSION:
        raise ValueError(
            f"capability matrix schema_version {version} != {CAPABILITY_SCHEMA_VERSION}"
        )
    probes: Dict[str, CapabilityProbe] = {}
    for entry in data.get("probes", []):
        probe = CapabilityProbe(**entry)
        probes[probe.probe_id] = probe
    return probes


def matrix_to_dict(probes: List[CapabilityProbe]) -> Dict[str, Any]:
    return {
        "schema": CAPABILITY_SCHEMA,
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "probes": [p.to_dict() for p in probes],
    }
