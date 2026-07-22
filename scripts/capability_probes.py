"""Live capability-probe runner (Milestone A3), wired through the A4/A5 paid path.

Each probe is one real API call whose ONLY purpose is to verify a capability the
capability matrix left UNVERIFIED (e.g. "does DeepSeek non-thinking mode emit
zero reasoning tokens?", "does the OpenAI no-reasoning setting stay at zero
reasoning tokens even with an output cap?"). Every probe:

1. estimates its cost in code (``src/costs.py``);
2. atomically reserves that cost against ``budget_state.json`` (fail-closed A4);
3. writes into its own append-only run directory (``src/run_manifest.py``, A5)
   with a request state journal;
4. token-preflights the request where a verified context window exists (A3);
5. calls the single generation choke point (``src/generation.py``);
6. sanitizes + stores the raw receipt (``src/receipts.py``, A7);
7. records a pass/fail/inconclusive capability record with the receipt hash and
   an expiration date (``src/capabilities.py``, A3); and
8. reconciles the reservation to the actual spend and logs it.

``run_probe`` is the testable core (inject a client); ``main`` drives it live.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from src import budget_state, costs, preflight  # noqa: E402
from src.capabilities import CapabilityProbe, matrix_to_dict  # noqa: E402
from src.generation import GenerationClient, GenerationRequest, cache_key  # noqa: E402
from src.receipts import write_receipt  # noqa: E402
from src.run_manifest import RunDirectory, build_manifest  # noqa: E402

PROBES_DIR = REPO_ROOT / "configs" / "probes"
RESULTS_DIR = REPO_ROOT / "data" / "results"
CAPABILITY_MATRIX_PATH = REPO_ROOT / "data" / "capabilities.json"
DEFAULT_EXPIRY_DAYS = 30


@dataclass
class ProbeSpec:
    probe_id: str
    provider: str
    model: str
    capability: str
    assertion: str  # reasoning_tokens_zero | prefill_continues | reasoning_field_present
    messages: List[Dict[str, Any]]
    max_tokens: int = 64
    temperature: float = 0.0
    thinking_setting: Any = "disabled"
    provider_route: Optional[str] = None
    prompt_template_version: str = "probe-v1"
    expires_on: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: Path) -> "ProbeSpec":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(**data)


def _evaluate(assertion: str, result: Any) -> str:
    """Map a generation result to a pass/fail/inconclusive capability status."""
    raw = result.raw_response or {}
    if assertion == "reasoning_tokens_zero":
        # PASS only if the provider reports exactly zero reasoning tokens.
        return "pass" if (result.reasoning_tokens or 0) == 0 else "fail"
    if assertion == "reasoning_field_present":
        has_field = (result.reasoning_tokens or 0) > 0 or _has_reasoning_content(raw)
        return "pass" if has_field else "fail"
    if assertion == "prefill_continues":
        # Native prefill must have been used and produced continuation text. The
        # researcher still eyeballs the transcript, so this is at most inconclusive
        # when the mechanism was not native.
        if result.prefill_mechanism != "native_prefill":
            return "inconclusive"
        return "pass" if (result.text or "").strip() else "inconclusive"
    return "inconclusive"


def _has_reasoning_content(raw: Dict[str, Any]) -> bool:
    try:
        blob = json.dumps(raw)
    except (TypeError, ValueError):
        return False
    # DeepSeek/GLM-direct expose "reasoning_content"; OpenRouter surfaces
    # "reasoning". Either counts as a raw reasoning field being present.
    return '"reasoning_content"' in blob or '"reasoning"' in blob


def run_probe(
    spec: ProbeSpec,
    client: GenerationClient,
    *,
    results_dir: Path = RESULTS_DIR,
    budget_path: Path = budget_state.DEFAULT_STATE_PATH,
    log_path: Path = costs.DEFAULT_LOG_PATH,
    today: Optional[date] = None,
    reserve_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Run one probe end-to-end and return a summary dict + CapabilityProbe.

    ``today``/``reserve_usd`` are injectable for tests. Returns the probe status,
    receipt hash, run directory, and the capability record.
    """
    run_id = f"probe_{spec.probe_id}"
    request = GenerationRequest(
        provider=spec.provider,
        model=spec.model,
        provider_route=spec.provider_route,
        messages=spec.messages,
        max_tokens=spec.max_tokens,
        temperature=spec.temperature,
        thinking_setting=spec.thinking_setting,
        prompt_template_version=spec.prompt_template_version,
    )

    # 1. estimate
    est = costs.compute_estimate([request])
    projected = reserve_usd if reserve_usd is not None else max(est.total_usd, 1e-6)

    # 2. reserve (fail-closed A4) — a conservative positive amount.
    reservation_id = f"{run_id}-{cache_key(request)[:8]}"
    budget_state.reserve(reservation_id, projected, run_id, path=budget_path, note=spec.capability)

    # 3. append-only run directory + lease + journal (A5)
    run_dir = results_dir / run_id
    manifest = build_manifest(
        run_id=run_id,
        experiment_kind="capability_probe",
        task_set_path="(none — capability probe)",
        task_set_sha256="0" * 64,
        selected_task_ids=[spec.probe_id],
        selection_args={"probe": spec.probe_id, "assertion": spec.assertion},
        config_sha256=hashlib.sha256(json.dumps(spec.__dict__, default=str, sort_keys=True).encode()).hexdigest(),
        prompt_template_version=spec.prompt_template_version,
        requested_settings={"thinking_setting": spec.thinking_setting, "temperature": spec.temperature},
        price_revision="prices-2026-07-08",
        provider_route=spec.provider_route,
        root=REPO_ROOT,
    )
    rd = RunDirectory.create(run_dir, manifest)
    response_key = rd.response_key({"task_id": spec.probe_id, "condition_id": "probe"})

    try:
        with rd.lease():
            rd.record_transition(response_key, "planned")

            # 4. preflight (best-effort: probes are tiny; skip if window UNVERIFIED)
            try:
                preflight.preflight_request(request)
            except preflight.UnknownContextWindowError:
                pass  # tiny probe prompt; no verified window yet

            rd.record_transition(response_key, "submitted")
            result = client.generate(request)  # 5. the single choke point
            rd.record_transition(response_key, "received")

            # 6. sanitized raw receipt (A7)
            receipt_path = rd.raw_dir / f"{spec.probe_id}.json"
            if result.raw_response is not None:
                write_receipt(receipt_path, result.raw_response)
            receipt_sha = _sha256_of(result.raw_response)

            # persist the response record
            rd.append_response({
                "task_id": spec.probe_id,
                "condition_id": "probe",
                "mechanism": result.prefill_mechanism,
                "replicate_index": 0,
                "text": result.text,
                "cache_key": result.cache_key,
                "timestamp": result.timestamp,
                "system_fingerprint": result.system_fingerprint,
                "finish_reason": result.finish_reason,
                "reasoning_tokens": result.reasoning_tokens,
                "applied_settings": result.applied_settings,
                "idempotency_key": result.idempotency_key,
                "raw_response_path": _rel(receipt_path),
            })
            rd.record_transition(response_key, "persisted")

            # 7. evaluate + capability record (A3)
            status = _evaluate(spec.assertion, result)
            the_date = today or date.today()
            expires = spec.expires_on or (the_date + timedelta(days=DEFAULT_EXPIRY_DAYS)).isoformat()
            probe_record = CapabilityProbe(
                probe_id=spec.probe_id,
                provider=spec.provider,
                model=spec.model,
                capability=spec.capability,
                status=status,
                verified_on=the_date.isoformat(),
                expires_on=expires,
                receipt_sha256=receipt_sha,
                notes=f"assertion={spec.assertion}; reasoning_tokens={result.reasoning_tokens}; mechanism={result.prefill_mechanism}",
            )

            # 8. reconcile reservation to actual spend + log
            actual = costs.incremental_cost_for_result(spec.model, result)
            budget_state.reconcile(reservation_id, actual, path=budget_path)
            if not result.cache_hit:
                costs.log_cost(
                    run_id=run_id, model=spec.model, provider=spec.provider,
                    input_tokens=result.input_tokens or 0,
                    output_tokens=result.output_tokens or 0,
                    cost_usd=actual,
                    cached_tokens=result.cached_input_tokens,
                    log_path=log_path,
                    emit=False,
                )
            rd.record_transition(response_key, "accounted")

            summary = {
                "probe_id": spec.probe_id,
                "status": status,
                "reasoning_tokens": result.reasoning_tokens,
                "prefill_mechanism": result.prefill_mechanism,
                "receipt_sha256": receipt_sha,
                "actual_usd": actual,
                "run_dir": _rel(run_dir),
            }
            rd.write_summary(summary)
            return {"summary": summary, "capability": probe_record}
    except Exception:
        # Release the reservation if we failed before reconciling it.
        try:
            budget_state.release(reservation_id, path=budget_path)
        except Exception:  # pragma: no cover - best effort
            pass
        raise


def _rel(p: Path) -> str:
    """Path relative to the repo root when possible, else the path as-is."""
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def _sha256_of(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live capability probes (Milestone A3).")
    parser.add_argument("--probe", dest="probes", action="append", required=True,
                        help="Path to a probe YAML (repeatable).")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    specs = [ProbeSpec.from_yaml(Path(p)) for p in args.probes]

    # Aggregate estimate + hard confirmation gate BEFORE any paid call.
    requests = [
        GenerationRequest(
            provider=s.provider, model=s.model, provider_route=s.provider_route,
            messages=s.messages, max_tokens=s.max_tokens, temperature=s.temperature,
            thinking_setting=s.thinking_setting, prompt_template_version=s.prompt_template_version,
        )
        for s in specs
    ]
    est = costs.estimate_batch(requests, confirmed=args.yes, interactive=not args.yes)
    print(f"Reserving against remaining budget: ${budget_state.load_state().remaining():.2f}")

    client = GenerationClient()
    records = []
    for spec in specs:
        print(f"\n=== probe {spec.probe_id} ({spec.provider}/{spec.model}) ===")
        out = run_probe(spec, client)
        print(f"  status: {out['summary']['status']}  reasoning_tokens={out['summary']['reasoning_tokens']}")
        records.append(out["capability"])

    CAPABILITY_MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPABILITY_MATRIX_PATH.write_text(
        json.dumps(matrix_to_dict(records), indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nWrote capability matrix: {CAPABILITY_MATRIX_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
