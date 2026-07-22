"""Rendered-request token preflight (Milestone A3; engineering risk E6).

Before any paid submission, the exactly-rendered chat request is priced in
tokens against the pinned model's context window, with output/final-answer space
reserved, and an oversize request is rejected BEFORE spend. A long HotpotQA
context plus an output reserve must never silently overflow the window and be
classified as a generation failure.

Context windows follow the same honesty discipline as the price table
(``src/costs.py``): an entry is ``verified`` with a source, or it is UNVERIFIED
with ``total_tokens=None`` and preflight FAILS CLOSED (``UnknownContextWindowError``)
rather than guessing. Fill verified windows from the Phase 0 capability matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.costs import _message_text, estimate_tokens

# --------------------------------------------------------------------------- #
# Context-window table (fill from the capability matrix; UNVERIFIED = fail closed)
# --------------------------------------------------------------------------- #


@dataclass
class ContextWindow:
    total_tokens: Optional[int]
    status: str = "UNVERIFIED — fill from capability matrix"
    last_verified: Optional[str] = None
    source: Optional[str] = None

    @property
    def is_verified(self) -> bool:
        return self.status == "verified" and self.total_tokens is not None


#: Keyed by the exact model id used in requests. All entries start UNVERIFIED so
#: preflight fails closed until the researcher records a verified window (with a
#: source) from docs/capability_matrix.md — mirroring the UNVERIFIED price policy.
CONTEXT_WINDOWS: Dict[str, ContextWindow] = {
    model: ContextWindow(total_tokens=None)
    for model in (
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-fable-5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "z-ai/glm-5.2",
        "deepseek-r1-distill-qwen-7b",
    )
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class UnknownContextWindowError(RuntimeError):
    """No verified context window for the model — cannot preflight (fail closed)."""


class ContextWindowExceededError(RuntimeError):
    """The rendered request plus reserved output would exceed the context window."""


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


@dataclass
class PreflightResult:
    model: str
    input_tokens: int
    token_method: str
    reserved_output_tokens: int
    context_window: int
    headroom_tokens: int
    fits: bool


def preflight_request(
    request: Any,
    *,
    windows: Optional[Dict[str, ContextWindow]] = None,
    reserved_output_tokens: Optional[int] = None,
) -> PreflightResult:
    """Token-preflight one request against its pinned context window.

    ``request`` exposes ``model``, ``messages``, and ``max_tokens`` (a
    ``GenerationRequest`` or a dict). Output space is reserved as
    ``reserved_output_tokens`` (default: the request's ``max_tokens``), because
    the completion must fit in the window alongside the prompt.

    Raises :class:`UnknownContextWindowError` for an unverified model and
    :class:`ContextWindowExceededError` when prompt + reserve exceeds the window.
    """
    table = windows if windows is not None else CONTEXT_WINDOWS
    model = _attr(request, "model")
    messages = _attr(request, "messages")
    max_tokens = int(_attr(request, "max_tokens"))
    reserve = int(reserved_output_tokens if reserved_output_tokens is not None else max_tokens)

    window = table.get(model)
    if window is None or not window.is_verified:
        raise UnknownContextWindowError(
            f"No verified context window for model {model!r}. Fill it in "
            f"preflight.CONTEXT_WINDOWS from the capability matrix before any run."
        )

    input_tokens, method = estimate_tokens(_message_text(messages), model)
    total = window.total_tokens  # verified => not None
    headroom = total - input_tokens - reserve  # type: ignore[operator]
    fits = headroom >= 0
    if not fits:
        raise ContextWindowExceededError(
            f"{model}: prompt {input_tokens} + reserved output {reserve} "
            f"= {input_tokens + reserve} exceeds context window {total} "
            f"by {-headroom} tokens. Reject/replace this task before spend."
        )
    return PreflightResult(
        model=model,
        input_tokens=input_tokens,
        token_method=method,
        reserved_output_tokens=reserve,
        context_window=total,  # type: ignore[arg-type]
        headroom_tokens=headroom,
        fits=True,
    )


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)
