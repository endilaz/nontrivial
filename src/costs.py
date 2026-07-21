"""Cost estimation + logging (CLAUDE.md ground rule 2).

Every batch is priced *in code* before launch — never by hand arithmetic — and
the confirmation gate blocks any batch estimated above $5 unless explicitly
confirmed. A running cost log is appended to ``logs/cost_log.csv`` and the hard
$450 project budget is guarded with 50% / 80% warnings.

Pricing honesty (ground rule 1): the price table carries a ``status`` per entry.
Entries we could verify are marked ``verified`` with a ``last_verified`` date and
``source``. Everything else is ``UNVERIFIED`` with ``price=None`` — and estimating
a batch that touches an UNVERIFIED model raises :class:`UnverifiedPriceError`
rather than inventing a number. Fill UNVERIFIED prices from the Phase 0
capability matrix, then flip the status.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "cost_log.csv"

#: Hard project API budget (ground rule 2).
PROJECT_BUDGET_USD = 450.0

#: Batch estimates above this require explicit confirmation (ground rule 2).
CONFIRM_THRESHOLD_USD = 5.0

#: Warn when cumulative cost crosses these fractions of the budget.
BUDGET_WARN_FRACTIONS = (0.50, 0.80)

CSV_HEADER = [
    "timestamp",
    "run_id",
    "model",
    "provider",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cost_usd",
    "cumulative_project_cost",
]


# --------------------------------------------------------------------------- #
# Price table
# --------------------------------------------------------------------------- #


@dataclass
class ModelPrice:
    """Per-model prices in USD per MILLION tokens.

    ``input_per_mtok`` / ``output_per_mtok`` / ``cached_input_per_mtok`` are
    ``None`` when unverified. ``status`` is ``"verified"`` or an ``"UNVERIFIED"``
    note. Estimating an UNVERIFIED model raises ``UnverifiedPriceError``.
    """

    input_per_mtok: Optional[float]
    output_per_mtok: Optional[float]
    cached_input_per_mtok: Optional[float] = None
    status: str = "UNVERIFIED — fill from capability matrix"
    last_verified: Optional[str] = None
    source: Optional[str] = None

    @property
    def is_verified(self) -> bool:
        return (
            self.status == "verified"
            and self.input_per_mtok is not None
            and self.output_per_mtok is not None
        )


_ANTHROPIC_SOURCE = (
    "https://platform.claude.com/docs/en/about-claude/pricing "
    "(capability matrix, accessed 2026-07-08)"
)
_OPENAI_SOURCE = (
    "https://developers.openai.com/api/docs/pricing "
    "(capability matrix, accessed 2026-07-08)"
)
_GEMINI_SOURCE = (
    "https://ai.google.dev/gemini-api/docs/pricing "
    "(capability matrix, accessed 2026-07-08)"
)
_DEEPSEEK_SOURCE = (
    "https://api-docs.deepseek.com/quick_start/pricing "
    "(capability matrix, accessed 2026-07-08)"
)

#: Central price table. Keyed by the exact model id used in requests.
#:
#: Verified entries come from the Phase 0 capability matrix
#: (docs/capability_matrix.md), which carries the full per-source citations;
#: batch prices (~50% where offered) are NOT reflected here — this table prices
#: the non-batch path and therefore over- rather than under-estimates batch
#: runs. Anything not confirmable from an official page stays UNVERIFIED with
#: price=None — we never invent a price (ground rule 1).
PRICES: Dict[str, ModelPrice] = {
    # --- Anthropic --------------------------------------------------------- #
    "claude-opus-4-8": ModelPrice(
        input_per_mtok=5.00, output_per_mtok=25.00, cached_input_per_mtok=0.50,
        status="verified", last_verified="2026-07-08", source=_ANTHROPIC_SOURCE,
    ),
    "claude-sonnet-4-6": ModelPrice(
        input_per_mtok=3.00, output_per_mtok=15.00, cached_input_per_mtok=0.30,
        status="verified", last_verified="2026-07-08", source=_ANTHROPIC_SOURCE,
    ),
    # Intro pricing through 2026-08-31; reverts to $3 / $15 / $0.30 from
    # 2026-09-01 — re-verify before any run after that date. Note: tokenizer
    # yields ~30% more tokens than Sonnet 4.6 for the same text.
    "claude-sonnet-5": ModelPrice(
        input_per_mtok=2.00, output_per_mtok=10.00, cached_input_per_mtok=0.20,
        status="verified", last_verified="2026-07-08", source=_ANTHROPIC_SOURCE,
    ),
    "claude-haiku-4-5": ModelPrice(
        input_per_mtok=1.00, output_per_mtok=5.00, cached_input_per_mtok=0.10,
        status="verified", last_verified="2026-07-08", source=_ANTHROPIC_SOURCE,
    ),
    # Cannot disable thinking — hidden-reasoning stratum if ever paneled.
    "claude-fable-5": ModelPrice(
        input_per_mtok=10.00, output_per_mtok=50.00, cached_input_per_mtok=1.00,
        status="verified", last_verified="2026-07-08", source=_ANTHROPIC_SOURCE,
    ),
    # --- OpenAI ------------------------------------------------------------ #
    "gpt-5.4": ModelPrice(
        input_per_mtok=2.50, output_per_mtok=15.00, cached_input_per_mtok=0.25,
        status="verified", last_verified="2026-07-08", source=_OPENAI_SOURCE,
    ),
    "gpt-5.4-mini": ModelPrice(
        input_per_mtok=0.75, output_per_mtok=4.50, cached_input_per_mtok=0.075,
        status="verified", last_verified="2026-07-08", source=_OPENAI_SOURCE,
    ),
    # --- Google ------------------------------------------------------------ #
    # Fails both Phase 3 hard gates (no prefill; thinking_level "minimal" does
    # not guarantee thinking off). Cache minimum 4,096 tokens.
    "gemini-3-flash-preview": ModelPrice(
        input_per_mtok=0.50, output_per_mtok=3.00, cached_input_per_mtok=0.05,
        status="verified", last_verified="2026-07-08", source=_GEMINI_SOURCE,
    ),
    # Fallback workhorse candidate (thinkingBudget: 0 verified; no prefill).
    "gemini-2.5-flash": ModelPrice(
        input_per_mtok=0.30, output_per_mtok=2.50, cached_input_per_mtok=0.03,
        status="verified", last_verified="2026-07-08", source=_GEMINI_SOURCE,
    ),
    # --- DeepSeek ----------------------------------------------------------- #
    # Recommended workhorse (pending researcher approval). Replaces the legacy
    # deepseek-chat / deepseek-reasoner names on 2026-07-24 — pin this id.
    # cached_input rate is DeepSeek's automatic cache-hit price.
    "deepseek-v4-flash": ModelPrice(
        input_per_mtok=0.14, output_per_mtok=0.28, cached_input_per_mtok=0.0028,
        status="verified", last_verified="2026-07-08", source=_DEEPSEEK_SOURCE,
    ),
    "deepseek-v4-pro": ModelPrice(
        input_per_mtok=0.435, output_per_mtok=0.87, cached_input_per_mtok=0.003625,
        status="verified", last_verified="2026-07-08", source=_DEEPSEEK_SOURCE,
    ),
    # --- Still UNVERIFIED --------------------------------------------------- #
    # Open-weight reasoning: price depends on which host serves it (OpenRouter
    # cheapest ~$0.93/$3.00 via DeepInfra; Z.ai/Together tier $1.40/$4.40).
    # Pin one provider for reproducibility, then verify that provider's price.
    "z-ai/glm-5.2": ModelPrice(
        input_per_mtok=0.93, output_per_mtok=2.94, cached_input_per_mtok=0.18,
        status="verified", last_verified="2026-07-21", source="https://openrouter.ai/z-ai/glm-5.2#providers"),
    # Bridge model: NO live serverless endpoint on Together/OpenRouter as of
    # 2026-07-08 — runs locally, so its cost is GPU time, not per-token. This
    # entry stays UNVERIFIED permanently unless a hosted endpoint is adopted.
    "deepseek-r1-distill-qwen-7b": ModelPrice(
        input_per_mtok=None, output_per_mtok=None, cached_input_per_mtok=None,
        status="UNVERIFIED — fill from capability matrix", last_verified=None, source=None,),
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class UnverifiedPriceError(RuntimeError):
    """Raised when a cost is requested for a model with no verified price."""


class ConfirmationRequired(RuntimeError):
    """Raised when a batch estimate exceeds the confirmation threshold."""


# --------------------------------------------------------------------------- #
# Token estimation
# --------------------------------------------------------------------------- #


def estimate_tokens(text: str, model: str) -> Tuple[int, str]:
    """Estimate the token count of ``text`` for ``model``.

    Uses tiktoken where a model-specific encoding is available; otherwise falls
    back to a chars/4 heuristic that is explicitly flagged in the returned
    method string (so downstream reports never mistake a guess for a count).

    Returns ``(n_tokens, method)`` where ``method`` is e.g. ``"tiktoken:o200k_base"``
    or ``"heuristic:chars/4"``.
    """
    text = text or ""
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            # Unknown (non-OpenAI) model: fall through to the heuristic rather
            # than pretend a default encoding is accurate for it.
            raise
        return len(enc.encode(text)), f"tiktoken:{enc.name}"
    except Exception:
        return max(1, (len(text) + 3) // 4), "heuristic:chars/4"


def _message_text(messages: Iterable[Dict[str, Any]]) -> str:
    """Flatten a message list to a single string for token estimation."""
    parts: List[str] = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(str(block))
        else:
            parts.append(str(content))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Cost math
# --------------------------------------------------------------------------- #


def cost_for_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    prices: Optional[Dict[str, ModelPrice]] = None,
) -> float:
    """USD cost for a single call's token usage.

    ``cached_input_tokens`` are billed at the cached-input rate (falling back to
    the normal input rate if no cached rate is recorded) and are treated as a
    subset already counted in ``input_tokens`` — so uncached input is
    ``input_tokens - cached_input_tokens``.

    Raises :class:`UnverifiedPriceError` if the model has no verified price.
    """
    table = prices if prices is not None else PRICES
    price = table.get(model)
    if price is None:
        raise UnverifiedPriceError(
            f"No price-table entry for model {model!r}. Add it to costs.PRICES "
            f"with a verified price before estimating."
        )
    if not price.is_verified:
        raise UnverifiedPriceError(
            f"Price for model {model!r} is UNVERIFIED ({price.status!r}). Fill it "
            f"from the Phase 0 capability matrix before estimating cost."
        )

    cached = max(0, cached_input_tokens)
    uncached_input = max(0, input_tokens - cached)
    cached_rate = (
        price.cached_input_per_mtok
        if price.cached_input_per_mtok is not None
        else price.input_per_mtok
    )
    cost = (
        uncached_input / 1_000_000 * price.input_per_mtok  # type: ignore[operator]
        + cached / 1_000_000 * cached_rate  # type: ignore[operator]
        + output_tokens / 1_000_000 * price.output_per_mtok  # type: ignore[operator]
    )
    return cost


# --------------------------------------------------------------------------- #
# Batch estimation + confirmation gate
# --------------------------------------------------------------------------- #


@dataclass
class BatchEstimate:
    total_usd: float = 0.0
    n_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    used_heuristic: bool = False
    per_model: Dict[str, Dict[str, float]] = field(default_factory=dict)


def compute_estimate(
    requests: Iterable[Any],
    prices: Optional[Dict[str, ModelPrice]] = None,
) -> BatchEstimate:
    """Price a batch of requests. Pure (no printing, no gating).

    Each request may be a ``GenerationRequest`` or any object/dict exposing
    ``model``, ``messages``, and ``max_tokens``. Output tokens are estimated as
    the full ``max_tokens`` (an upper bound); the forced-immediate-answer
    re-injections in Phase 3 will usually be far under this.

    Raises :class:`UnverifiedPriceError` for any UNVERIFIED model.
    """
    est = BatchEstimate()
    for req in requests:
        model = _attr(req, "model")
        messages = _attr(req, "messages")
        max_tokens = _attr(req, "max_tokens")

        in_tokens, method = estimate_tokens(_message_text(messages), model)
        out_tokens = int(max_tokens)
        if method.startswith("heuristic"):
            est.used_heuristic = True

        cost = cost_for_usage(model, in_tokens, out_tokens, prices=prices)

        est.total_usd += cost
        est.n_requests += 1
        est.total_input_tokens += in_tokens
        est.total_output_tokens += out_tokens
        bucket = est.per_model.setdefault(
            model, {"n": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        bucket["n"] += 1
        bucket["input_tokens"] += in_tokens
        bucket["output_tokens"] += out_tokens
        bucket["cost_usd"] += cost
    return est


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def print_estimate(est: BatchEstimate, file=sys.stdout) -> None:
    """Print an itemised, human-readable estimate."""
    print("=" * 60, file=file)
    print("BATCH COST ESTIMATE", file=file)
    print("-" * 60, file=file)
    for model, b in sorted(est.per_model.items()):
        print(
            f"  {model:<32} n={int(b['n']):>4}  "
            f"in={int(b['input_tokens']):>9}  out={int(b['output_tokens']):>9}  "
            f"${b['cost_usd']:.4f}",
            file=file,
        )
    print("-" * 60, file=file)
    print(
        f"  TOTAL  n={est.n_requests}  in={est.total_input_tokens}  "
        f"out={est.total_output_tokens}  ${est.total_usd:.4f}",
        file=file,
    )
    if est.used_heuristic:
        print(
            "  NOTE: some token counts use the chars/4 HEURISTIC (no tiktoken "
            "encoding for that model) — treat totals as approximate.",
            file=file,
        )
    print("=" * 60, file=file)


def estimate_batch(
    requests: Iterable[Any],
    prices: Optional[Dict[str, ModelPrice]] = None,
    *,
    confirmed: bool = False,
    threshold_usd: float = CONFIRM_THRESHOLD_USD,
    interactive: bool = False,
    verbose: bool = True,
) -> BatchEstimate:
    """Estimate a batch, print it, and enforce the >$5 confirmation gate.

    The gate (ground rule 2): if the estimate exceeds ``threshold_usd`` and the
    caller has not passed ``confirmed=True``, this either prompts on an
    interactive TTY (``interactive=True``) or raises :class:`ConfirmationRequired`.
    """
    requests = list(requests)
    est = compute_estimate(requests, prices=prices)
    if verbose:
        print_estimate(est)

    if est.total_usd > threshold_usd and not confirmed:
        msg = (
            f"Batch estimate ${est.total_usd:.2f} exceeds the "
            f"${threshold_usd:.2f} confirmation threshold."
        )
        if interactive and sys.stdin is not None and sys.stdin.isatty():
            reply = input(f"{msg} Proceed? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                raise ConfirmationRequired(msg + " (declined at prompt)")
        else:
            raise ConfirmationRequired(
                msg + " Pass confirmed=True (or --yes) to proceed."
            )
    return est


# --------------------------------------------------------------------------- #
# Cost logging + budget guard
# --------------------------------------------------------------------------- #


def _read_cumulative(log_path: Path) -> float:
    """Sum the cost_usd column of an existing log (0 if absent)."""
    if not log_path.exists():
        return 0.0
    total = 0.0
    with log_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                total += float(row.get("cost_usd", 0) or 0)
            except (TypeError, ValueError):
                continue
    return total


def check_budget(
    cumulative_before: float,
    cumulative_after: float,
    budget: float = PROJECT_BUDGET_USD,
) -> List[str]:
    """Return warning strings for any budget fraction newly crossed."""
    warnings: List[str] = []
    for frac in BUDGET_WARN_FRACTIONS:
        threshold = budget * frac
        if cumulative_before < threshold <= cumulative_after:
            warnings.append(
                f"BUDGET ALERT: cumulative API cost ${cumulative_after:.2f} has "
                f"crossed {int(frac * 100)}% of the ${budget:.0f} budget "
                f"(${threshold:.2f}). Stop and alert the researcher (ground rule 2)."
            )
    return warnings


def log_cost(
    run_id: str,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    cached_tokens: int = 0,
    log_path: Path = DEFAULT_LOG_PATH,
    budget: float = PROJECT_BUDGET_USD,
    emit: bool = True,
) -> float:
    """Append one row to the cost log and return the new cumulative total.

    Creates the file with a header if it does not yet exist. Emits prominent
    budget warnings (to stderr) when cumulative cost crosses 50% / 80%.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cumulative_before = _read_cumulative(log_path)
    cumulative_after = cumulative_before + cost_usd

    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(CSV_HEADER)
        writer.writerow(
            [
                datetime.now(timezone.utc).isoformat(),
                run_id,
                model,
                provider,
                input_tokens,
                output_tokens,
                cached_tokens,
                f"{cost_usd:.6f}",
                f"{cumulative_after:.6f}",
            ]
        )

    if emit:
        for warning in check_budget(cumulative_before, cumulative_after, budget):
            print("\n" + "!" * 70, file=sys.stderr)
            print(warning, file=sys.stderr)
            print("!" * 70 + "\n", file=sys.stderr)

    return cumulative_after
