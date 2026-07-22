# Live capability probes (Milestone A3)

Each probe is one real API call verifying an UNVERIFIED capability-matrix cell.
Run via `scripts/capability_probes.py --probe configs/probes/<name>.yaml`. Every
probe reserves its cost against `budget_state.json` first (fail-closed), writes
an append-only run directory under `data/results/probe_<id>/`, sanitizes its raw
receipt, and records a pass/fail/inconclusive capability with a receipt hash and
expiry into `data/capabilities.json`.

## The four probes (plan.md §A3)

| # | File | Status | Verifies |
|---|---|---|---|
| 2 | `deepseek_prefill.yaml` | ready | DeepSeek beta prefix completion continues the assistant prefix |
| 3 | `openai_no_reasoning.yaml` | ready | OpenAI `reasoning_effort:"none"` + output cap → 0 reasoning tokens (bug check) |
| 1 | deepseek non-thinking | **blocked** | non-thinking mode → 0 reasoning tokens |
| 4 | glm reasoning field | **blocked** | GLM-5.2 on the pinned route returns the reasoning field when enabled |

## Blocked probes — decisions needed before their configs are written

- **#1 DeepSeek non-thinking:** the exact request parameter that selects
  non-thinking mode on `deepseek-v4-flash` is UNVERIFIED (matrix open-Q #7). The
  adapter fails closed on the string `disabled`, so a probe needs the real dict.
  Resolve from the DeepSeek API reference (or use the legacy `deepseek-chat`
  alias as a cross-check before its 2026-07-24 sunset), then the config is a
  one-liner.
- **#4 GLM-5.2:** pin one OpenRouter upstream provider (`provider_route`) for
  reproducibility — DeepInfra ($0.93/$3.00) is cheapest; Z.AI/Together/Fireworks
  are the $1.40/$4.40 tier. GLM's thinking toggle is known
  (`{"thinking": {"type": "enabled"}}`).
