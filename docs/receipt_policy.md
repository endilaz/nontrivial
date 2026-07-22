# Raw provider-receipt policy (Milestone A7)

Raw provider responses are kept for audit (ground rule 1: keep the receipts), but
they are sensitive: they can contain echoed credentials, and provider terms may
restrict redistribution. This policy governs how receipts are stored and released.

## Storage rules (enforced by `src/receipts.py`)

1. **Field allowlist.** Only expected provider response fields are retained
   (`DEFAULT_ALLOWED_FIELDS`). Anything outside the allowlist is dropped, and any
   secret-named key (`authorization`, `api_key`, `access_token`, `cookie`, …) is
   dropped even if it would otherwise be allowlisted.
2. **Secret redaction.** Remaining string values are scanned for secret shapes
   (OpenAI/Anthropic/Google keys, bearer tokens, JWTs, AWS key ids) and replaced
   with `[REDACTED]` before the receipt is written.
3. **Restricted permissions.** Receipt files are written `0o600` (owner
   read/write only).
4. **`.env` loading is explicit.** Keys are loaded only from the project `.env`
   path, never via environment-file discovery (`src/generation.py`).

## Immutability

- Cache receipts are content-addressed by the semantic request envelope hash and
  verified against their filename before use (`src/generation.py`).
- `force_fresh` writes a NEW immutable receipt and archives the previous one
  under `data/cache/v2/history/`; it never overwrites an existing receipt.
- Malformed or envelope-mismatched entries are quarantined under
  `data/cache/v2/quarantine/`, not deleted.

## Retention

- Raw receipts live under `data/cache/` (gitignored) and per-run `raw/`
  directories. They are retained for the life of the project for audit and are
  not pruned automatically.
- Invalidated receipts are preserved under `.../invalidated/2026-07-21/` and
  recorded by hash in `data/invalidation/manifest_2026-07-21.json`.

## Release — deny by default

Raw receipts are **not** published with the repository or dataset by default.
Before any receipt is released:

1. Confirm the provider's terms permit redistribution of response content.
2. Complete a privacy review for any user/content data in the receipts.
3. Publish only sanitized receipts (allowlist + redaction applied) if approved.

Release scripts must treat raw receipts as deny-by-default and require an
explicit, reviewed opt-in to include any of them.
