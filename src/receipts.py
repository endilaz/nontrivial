"""Raw provider-receipt handling with redaction + restricted permissions (A7).

Raw provider responses are kept for audit (ground rule 1), but they can carry
secrets (a leaked ``authorization`` header, an echoed key) and are not safe to
publish. This module enforces plan.md §A7's receipt policy:

- **Secret scanning/redaction** — known secret shapes (API keys, bearer tokens,
  JWTs) are replaced with ``[REDACTED]`` recursively before a receipt is stored.
- **Field allowlist** — only expected provider fields are retained; anything
  else (e.g. an ``authorization`` echo) is dropped.
- **Restricted permissions** — receipt files are written ``0o600``.
- **Release deny-by-default** — see ``docs/receipt_policy.md``; raw receipts are
  not published until provider terms + privacy review allow it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

REDACTION = "[REDACTED]"

#: Secret-shaped substrings replaced anywhere they appear in a string value.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),            # OpenAI-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),         # Anthropic keys
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),            # Google API keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),  # bearer tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"),  # JWTs
    re.compile(r"AKIA[0-9A-Z]{16}"),                   # AWS access key ids
]

#: Top-level keys whose NAME indicates a secret/credential — always dropped.
_SECRET_KEY_NAMES = {
    "authorization", "api_key", "apikey", "api-key", "x-api-key",
    "access_token", "refresh_token", "secret", "password", "cookie", "set-cookie",
}

#: Conservative default allowlist of provider response fields worth keeping.
#: Covers OpenAI-compatible, Anthropic, and Gemini response shapes.
DEFAULT_ALLOWED_FIELDS = frozenset({
    "id", "object", "created", "model", "system_fingerprint",
    "choices", "usage", "finish_reason", "prompt_tokens_details",
    "completion_tokens_details",
    # Anthropic
    "type", "role", "content", "stop_reason", "stop_sequence",
    "input_tokens", "output_tokens", "cache_read_input_tokens",
    # Gemini
    "candidates", "usage_metadata", "response_id", "prompt_token_count",
    "candidates_token_count", "cached_content_token_count",
})


def redact_secrets(obj: Any) -> Any:
    """Recursively replace secret-shaped strings with ``[REDACTED]``."""
    if isinstance(obj, str):
        out = obj
        for pat in _SECRET_PATTERNS:
            out = pat.sub(REDACTION, out)
        return out
    if isinstance(obj, dict):
        return {k: redact_secrets(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    return obj


def sanitize_receipt(
    raw: Dict[str, Any],
    allowed_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Apply the field allowlist and secret redaction to a raw receipt.

    Top-level keys not in the allowlist are dropped; secret-named keys are always
    dropped even if allowlisted; remaining values are redaction-scanned.
    """
    allowed = frozenset(allowed_fields) if allowed_fields is not None else DEFAULT_ALLOWED_FIELDS
    cleaned: Dict[str, Any] = {}
    for key, value in raw.items():
        if key.lower() in _SECRET_KEY_NAMES:
            continue
        if key not in allowed:
            continue
        cleaned[key] = redact_secrets(value)
    return cleaned


def write_receipt(
    path: Union[str, Path],
    raw: Dict[str, Any],
    *,
    allowed_fields: Optional[Iterable[str]] = None,
) -> Path:
    """Sanitize ``raw`` and write it to ``path`` with ``0o600`` permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = sanitize_receipt(raw, allowed_fields=allowed_fields)
    # os.open with mode 0o600 so the file is never briefly world-readable.
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(path, 0o600)  # enforce even if umask/O_CREAT differed
    return path
