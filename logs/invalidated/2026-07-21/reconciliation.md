# Cost-ledger reconciliation — 2026-07-21 invalidation

The original `logs/cost_log.csv` is preserved in this directory unchanged. It is
**invalidated as a remaining-budget source** and must not be trusted for the
$450 hard budget until reconciled against provider dashboards.

## Why the repository ledger cannot be trusted

1. **Duplicate cache-hit accounting.** Re-running the baseline runners re-logged
   cache hits as if they were fresh paid calls. In the preserved ledger, the row
   clusters stamped `2026-07-21T12:09:53`, `12:15:22`, `12:18:45`, `13:22:19`,
   and `13:52:0x` repeat earlier calls (identical token counts/costs) — these are
   local disk-cache hits that should have cost `$0`. This is exactly the blocker
   A4 fixes (`incremental_cost_for_result == 0` for disk-cache hits).
2. **Untracked judge spend.** The git history (`7df01d9`,
   "using openrouter free model llm-as-judge") shows LLM-as-judge calls that were
   never routed through `src/costs.py`, so they are **absent** from this ledger.
   True historical spend therefore cannot be reconstructed from repository
   artifacts alone.

## Repository-recorded figure (NOT authoritative)

- Final `cumulative_project_cost` in the preserved ledger: **≈ $0.173447**.
- This is simultaneously *inflated* (duplicate cache hits) and *incomplete*
  (missing judge calls). It is recorded here only for audit traceability.

## Required researcher action (decision gate, blocks A4)

Record the **actual** spend from each provider dashboard for the project period,
independent of this ledger. A4's `budget_state.json` must be seeded from a
researcher-approved historical-spend figure (or a conservative upper bound) —
paid calls stay blocked until it is.

| Provider | Dashboard total (USD) | Period covered | Verified by | Date |
|---|---|---|---|---|
| DeepSeek | 0.01 | 7/21 | Daniel | 7/22 |
| OpenRouter (GLM-5.2) | 0.05 | 7/21 | Daniel | 7/22 |
| OpenAI (gpt-5.4) | 0.00 | N/A | Daniel | 7/22 |
| **Total historical spend** | 0.08 | 7/21 | Daniel | 7/22 |

Once filled in, carry the approved total into `budget_state.json` (Milestone A4)
and cite this file as its provenance.
