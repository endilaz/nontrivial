"""Provider-agnostic generation client with retry/backoff and a disk response cache.

This is the single choke point through which every API call in the project
flows. It exists to satisfy three CLAUDE.md ground rules:

  * Ground rule 4 (reproducibility): every request records its full parameters,
    including the thinking/reasoning setting and the prefill mechanism actually
    used.
  * Ground rule 7 (secrets): API keys are read from ``.env`` via python-dotenv
    and are NEVER printed or logged.
  * Ground rule 8 (response caching): every response is cached to disk keyed by
    (provider, model, full messages, sampling params, thinking setting,
    replicate_index). Re-runs hit the cache unless ``force_fresh`` is set.

Supported providers:
  * ``openai``      — OpenAI chat-completions
  * ``anthropic``   — Anthropic Messages API
  * ``google``      — Google Gemini (google-genai)
  * ``openrouter``  — OpenAI-compatible endpoint (custom base_url)
  * ``together``    — OpenAI-compatible endpoint (custom base_url)
  * ``deepseek``    — DeepSeek first-party API (OpenAI-compatible; native
                      prefill via the beta Chat Prefix Completion endpoint)

Design notes
------------
* Provider SDKs are imported *lazily* inside each adapter, so importing this
  module (and running the unit tests, which mock the network) never requires the
  SDKs to be installed or any key to be present.
* The actual network call lives in :meth:`GenerationClient._invoke_provider`.
  Retry/backoff wraps that method; the disk cache wraps the retry. Tests mock
  ``_invoke_provider`` to exercise retry and caching without touching the
  network.

CAPABILITY-MATRIX CAVEATS (Phase 0, researcher's call — not silently handled here):
* Several 2026 frontier models reject a trailing assistant "prefill" message
  (e.g. current Anthropic Opus/Sonnet-5/Fable, and some reasoning models).
  This client passes the prefill through natively and RECORDS the mechanism; it
  does not pre-judge whether a given model accepts it. Confirm per model in the
  capability matrix and set ``PROVIDER_PREFILL_SUPPORT`` / the run config
  accordingly (see the hidden-reasoning policy in CLAUDE.md Section 4).
* Some frontier models also reject an explicit ``temperature``. We forward the
  configured temperature and let the provider error surface; do not assume
  temperature 0 works everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

try:  # python-dotenv is a hard dependency, but keep import failure legible
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default on-disk response-cache directory (relative to repo root).
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

#: env-var name that holds each provider's key. Never print these VALUES.
PROVIDER_ENV_VARS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together": "TOGETHER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

#: base_url for the OpenAI-compatible providers.
OPENAI_COMPATIBLE_BASE_URLS: Dict[str, Optional[str]] = {
    "openai": None,  # SDK default
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com",
}

#: DeepSeek's Chat Prefix Completion (native prefill) lives on the beta base URL:
#: https://api-docs.deepseek.com/guides/chat_prefix_completion (2026-07-08).
DEEPSEEK_BETA_BASE_URL = "https://api.deepseek.com/beta"

#: Whether a provider supports a NATIVE assistant-prefill (a trailing assistant
#: message that the model continues). When False, the prefill text is folded
#: into a strict "state the answer only" instruction and the mechanism is
#: recorded as ``instruction_only`` — the prefill is never silently dropped.
#:
#: Values set from the Phase 0 capability matrix (docs/capability_matrix.md,
#: 2026-07-08): Anthropic's current AND legacy models 400 on prefill; OpenAI
#: and Gemini have no prefill mechanism; only DeepSeek's first-party API (beta
#: Chat Prefix Completion) supports it among API providers. Per the researcher
#: decision (2026-07-08), instruction-only is the PRIMARY Phase 3 mechanism on
#: all models; prefill additionally runs where True here (robustness arm).
PROVIDER_PREFILL_SUPPORT: Dict[str, bool] = {
    "openai": False,
    "anthropic": False,
    "google": False,
    "openrouter": False,
    "together": False,
    "deepseek": True,  # beta Chat Prefix Completion — confirm in smoke test
}

#: HTTP statuses we retry on (rate limit, transient server / gateway errors,
#: 529 = Anthropic "overloaded").
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}

#: Prefill-mechanism labels recorded in the result.
PREFILL_NATIVE = "native_prefill"
PREFILL_INSTRUCTION = "instruction_only"
PREFILL_NONE = None  # no prefill was requested


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


class GenerationRequest(BaseModel):
    """A single generation request.

    ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts. Roles are
    ``system`` / ``user`` / ``assistant``. A trailing ``assistant`` message is
    treated as a prefill (forced-immediate-answer re-injection, Phase 3).
    """

    provider: str
    model: str
    messages: List[Dict[str, Any]]
    temperature: float = 0.0
    max_tokens: int = 512
    seed: Optional[int] = None

    # thinking/reasoning setting is ALWAYS recorded (hidden-reasoning policy,
    # CLAUDE.md Section 4). Examples:
    #   "disabled"        -> native thinking turned off where the API allows it
    #   "none_supported"  -> provider/model has no thinking mode to disable
    #   {"type": "adaptive", ...} / {"thinking_budget": 0} -> provider-specific
    thinking_setting: Union[str, Dict[str, Any]] = "disabled"

    # replicate_index lets the Phase 3 nondeterminism control deliberately
    # re-run an identical request without a cache collision: it is part of the
    # cache key, so replicate 0 and replicate 1 get separate cache entries even
    # though every other parameter is identical.
    replicate_index: int = 0

    # force_fresh bypasses the cache READ (still writes). NOT part of the cache
    # key — it is a control flag, not a request parameter.
    force_fresh: bool = False

    model_config = {"extra": "forbid"}


class GenerationResult(BaseModel):
    """The outcome of a generation call (fresh or served from cache)."""

    text: str
    provider: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: Optional[float] = None
    timestamp: str
    cache_hit: bool = False
    cache_key: str
    prefill_mechanism: Optional[str] = None
    system_fingerprint: Optional[str] = None
    # Raw, JSON-serialisable provider response (ground rule 1: keep the receipts).
    raw_response: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class MissingAPIKeyError(RuntimeError):
    """Raised when the env var for a provider's key is unset/empty."""


class UnknownProviderError(ValueError):
    """Raised for a provider name we do not have an adapter for."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: Dict[str, Any]) -> str:
    """Deterministic JSON used for hashing (sorted keys, no incidental spaces)."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def cache_key(request: GenerationRequest) -> str:
    """SHA-256 over the canonical serialisation of the cache-relevant fields.

    The key deliberately includes the FULL messages (system + user + any
    assistant prefill), sampling params, thinking setting, and replicate_index.
    It deliberately EXCLUDES ``force_fresh`` (a control flag, not a request
    parameter).
    """
    payload = {
        "provider": request.provider,
        "model": request.model,
        "messages": request.messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "seed": request.seed,
        "thinking_setting": request.thinking_setting,
        "replicate_index": request.replicate_index,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _to_serializable(obj: Any) -> Any:
    """Best-effort conversion of an SDK response object to plain JSON data."""
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # pragma: no cover - defensive
                pass
    try:
        return json.loads(json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:  # pragma: no cover - defensive
        return str(obj)


def _extract_status(exc: Exception) -> Optional[int]:
    """Pull an HTTP status code off a provider exception, if present."""
    for attr in ("status_code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        val = getattr(resp, "status_code", None)
        if isinstance(val, int):
            return val
    return None


def _is_retryable(exc: Exception) -> bool:
    """True if the exception looks like a transient rate-limit/5xx/timeout."""
    status = _extract_status(exc)
    if status in RETRYABLE_STATUS:
        return True
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    return any(
        token in name
        for token in ("timeout", "connection", "ratelimit", "overloaded", "serviceunavailable")
    )


def _split_prefill(messages: List[Dict[str, Any]]):
    """Return (leading_messages, prefill_content_or_None).

    A trailing ``assistant``-role message is treated as the prefill.
    """
    if messages and messages[-1].get("role") == "assistant":
        return messages[:-1], messages[-1].get("content", "")
    return messages, None


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class GenerationClient:
    """Provider-agnostic client: cache -> retry -> provider adapter.

    Parameters
    ----------
    cache_dir:
        Directory for the on-disk response cache. Created if missing.
    max_retries:
        Maximum number of RETRIES (so up to ``max_retries + 1`` attempts).
    base_delay, max_delay:
        Exponential backoff bounds, in seconds.
    sleep_fn:
        Injected for testability (tests pass a no-op).
    load_env:
        If True, call ``load_dotenv`` once at construction so ``.env`` keys are
        available. Set False in tests.
    logger:
        Optional callable ``logger(msg: str)`` for retry / cache messages. Must
        never be handed key material (this class only passes non-secret text).
    """

    def __init__(
        self,
        cache_dir: Union[str, Path] = DEFAULT_CACHE_DIR,
        max_retries: int = 6,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        load_env: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep_fn
        self._log = logger or (lambda _msg: None)
        if load_env and load_dotenv is not None:
            load_dotenv()

    # -- public API -------------------------------------------------------- #

    def cache_key(self, request: GenerationRequest) -> str:
        """Expose the module-level :func:`cache_key` as a method for convenience."""
        return cache_key(request)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Return a :class:`GenerationResult`, from cache if possible."""
        key = cache_key(request)

        if not request.force_fresh:
            cached = self._read_cache(key)
            if cached is not None:
                self._log(f"cache hit {key[:12]} ({request.provider}/{request.model})")
                return cached

        start = time.perf_counter()
        invoked = self._with_retry(lambda: self._invoke_provider(request), request)
        latency = time.perf_counter() - start

        result = GenerationResult(
            text=invoked["text"],
            provider=request.provider,
            model=request.model,
            input_tokens=invoked.get("input_tokens"),
            output_tokens=invoked.get("output_tokens"),
            latency_s=latency,
            timestamp=_now_iso(),
            cache_hit=False,
            cache_key=key,
            prefill_mechanism=invoked.get("prefill_mechanism"),
            system_fingerprint=invoked.get("system_fingerprint"),
            raw_response=invoked.get("raw_response"),
        )
        self._write_cache(key, result)
        return result

    # -- cache ------------------------------------------------------------- #

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> Optional[GenerationResult]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - corrupt cache
            return None
        result = GenerationResult.model_validate(data)
        result.cache_hit = True  # mark provenance; stored copy has cache_hit=False
        return result

    def _write_cache(self, key: str, result: GenerationResult) -> None:
        # Store the canonical copy with cache_hit=False; reads flip it to True.
        stored = result.model_copy(update={"cache_hit": False})
        tmp = self._cache_path(key).with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(stored.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._cache_path(key))  # atomic-ish on same filesystem

    # -- retry ------------------------------------------------------------- #

    def _with_retry(self, fn: Callable[[], Dict[str, Any]], request: GenerationRequest) -> Dict[str, Any]:
        attempt = 0
        while True:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - we re-raise non-retryable
                if attempt >= self.max_retries or not _is_retryable(exc):
                    raise
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                delay += random.uniform(0, self.base_delay)  # full jitter add-on
                status = _extract_status(exc)
                self._log(
                    f"retry {attempt + 1}/{self.max_retries} after {delay:.2f}s "
                    f"({request.provider}/{request.model}, status={status}, "
                    f"{type(exc).__name__})"
                )
                self._sleep(delay)
                attempt += 1

    # -- key handling ------------------------------------------------------ #

    def _get_key(self, provider: str) -> str:
        env_var = PROVIDER_ENV_VARS.get(provider)
        if env_var is None:
            raise UnknownProviderError(f"No env var configured for provider {provider!r}")
        key = os.environ.get(env_var, "")
        if not key:
            # Report the missing VARIABLE name, never a value.
            raise MissingAPIKeyError(
                f"Environment variable {env_var} is not set (needed for provider "
                f"{provider!r}). Copy .env.template to .env and fill it in."
            )
        return key

    # -- provider dispatch ------------------------------------------------- #

    def _invoke_provider(self, request: GenerationRequest) -> Dict[str, Any]:
        """Do the actual network call. Retry wraps this; the cache wraps retry.

        Returns a normalised dict with keys: text, input_tokens, output_tokens,
        prefill_mechanism, system_fingerprint, raw_response.
        """
        provider = request.provider
        if provider in ("openai", "openrouter", "together", "deepseek"):
            return self._invoke_openai_compatible(request)
        if provider == "anthropic":
            return self._invoke_anthropic(request)
        if provider == "google":
            return self._invoke_google(request)
        raise UnknownProviderError(f"Unknown provider {provider!r}")

    def _prefill_mode(self, provider: str, has_prefill: bool) -> Optional[str]:
        if not has_prefill:
            return PREFILL_NONE
        return PREFILL_NATIVE if PROVIDER_PREFILL_SUPPORT.get(provider, False) else PREFILL_INSTRUCTION

    # -- OpenAI-compatible adapter ----------------------------------------- #

    def _invoke_openai_compatible(self, request: GenerationRequest) -> Dict[str, Any]:
        import openai  # lazy

        api_key = self._get_key(request.provider)
        base_url = OPENAI_COMPATIBLE_BASE_URLS.get(request.provider)

        leading, prefill = _split_prefill(request.messages)
        mode = self._prefill_mode(request.provider, prefill is not None)

        messages = list(leading)
        if prefill is not None:
            if mode == PREFILL_NATIVE and request.provider == "deepseek":
                # DeepSeek Chat Prefix Completion (beta): the trailing assistant
                # message carries "prefix": true and the call must go to the
                # /beta base URL. Interaction with thinking mode is UNVERIFIED
                # (capability matrix open question) — confirm in the smoke test.
                base_url = DEEPSEEK_BETA_BASE_URL
                messages.append({"role": "assistant", "content": prefill, "prefix": True})
            elif mode == PREFILL_NATIVE:
                messages.append({"role": "assistant", "content": prefill})
            else:  # instruction_only: fold prefill into a strict instruction
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "State the final answer only, with no further reasoning, "
                            f"continuing exactly from: {prefill!r}"
                        ),
                    }
                )

        client = openai.OpenAI(api_key=api_key, base_url=base_url) if base_url else openai.OpenAI(api_key=api_key)

        kwargs: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            # NOTE: some newer models require `max_completion_tokens` instead of
            # `max_tokens` and reject `temperature`; resolve per model in the
            # capability matrix. Scaffold uses the classic chat-completions shape.
            "max_tokens": request.max_tokens,
        }
        if request.provider == "openai":
            kwargs.update({"max_completion_tokens": request.max_tokens})
            kwargs.pop("max_tokens")
        if request.seed is not None:
            kwargs["seed"] = request.seed

        resp = client.chat.completions.create(**kwargs)
        raw = _to_serializable(resp)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return {
            "text": text,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "prefill_mechanism": mode,
            "system_fingerprint": getattr(resp, "system_fingerprint", None),
            "raw_response": raw,
        }

    # -- Anthropic adapter ------------------------------------------------- #

    def _invoke_anthropic(self, request: GenerationRequest) -> Dict[str, Any]:
        import anthropic  # lazy

        api_key = self._get_key("anthropic")
        client = anthropic.Anthropic(api_key=api_key)

        # Anthropic takes `system` separately from the message list.
        system_parts = [m["content"] for m in request.messages if m.get("role") == "system"]
        convo = [m for m in request.messages if m.get("role") != "system"]
        leading, prefill = _split_prefill(convo)
        mode = self._prefill_mode("anthropic", prefill is not None)

        messages = list(leading)
        if prefill is not None and mode == PREFILL_NATIVE:
            # Native prefill = final assistant message the model continues.
            # CAVEAT: current Anthropic frontier models (Opus 4.7/4.8, Sonnet 5,
            # Fable 5) REJECT this with a 400. Verify per model before relying on
            # it for the forced-immediate-answer re-injection (CLAUDE.md Phase 3).
            messages.append({"role": "assistant", "content": prefill})
        elif prefill is not None:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "State the final answer only, with no further reasoning, "
                        f"continuing exactly from: {prefill!r}"
                    ),
                }
            )

        kwargs: Dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if isinstance(request.thinking_setting, dict):
            kwargs["thinking"] = request.thinking_setting  # provider-specific dict

        resp = client.messages.create(**kwargs)
        raw = _to_serializable(resp)
        text = "".join(
            getattr(block, "text", "") for block in resp.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(resp, "usage", None)
        return {
            "text": text,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "prefill_mechanism": mode,
            "system_fingerprint": None,  # Anthropic has no system_fingerprint field
            "raw_response": raw,
        }

    # -- Google Gemini adapter --------------------------------------------- #

    def _invoke_google(self, request: GenerationRequest) -> Dict[str, Any]:
        from google import genai  # lazy
        from google.genai import types  # lazy

        api_key = self._get_key("google")
        client = genai.Client(api_key=api_key)

        system_parts = [m["content"] for m in request.messages if m.get("role") == "system"]
        convo = [m for m in request.messages if m.get("role") != "system"]
        leading, prefill = _split_prefill(convo)
        mode = self._prefill_mode("google", prefill is not None)

        # Gemini roles: user -> "user", assistant -> "model".
        contents = []
        for m in leading:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=str(m.get("content", "")))]))
        if prefill is not None and mode == PREFILL_NATIVE:
            contents.append(types.Content(role="model", parts=[types.Part(text=str(prefill))]))
        elif prefill is not None:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=(
                        "State the final answer only, with no further reasoning, "
                        f"continuing exactly from: {prefill!r}"
                    ))],
                )
            )

        config_kwargs: Dict[str, Any] = {
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
        }
        if system_parts:
            config_kwargs["system_instruction"] = "\n\n".join(system_parts)
        if request.seed is not None:
            config_kwargs["seed"] = request.seed
        # Disable Gemini "thinking" by setting a zero budget when requested.
        if request.thinking_setting == "disabled":
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        elif isinstance(request.thinking_setting, dict):
            config_kwargs["thinking_config"] = types.ThinkingConfig(**request.thinking_setting)

        resp = client.models.generate_content(
            model=request.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        raw = _to_serializable(resp)
        text = getattr(resp, "text", "") or ""
        usage = getattr(resp, "usage_metadata", None)
        return {
            "text": text,
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "prefill_mechanism": mode,
            "system_fingerprint": getattr(resp, "response_id", None),
            "raw_response": raw,
        }
