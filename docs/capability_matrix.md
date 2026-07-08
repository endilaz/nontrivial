# Phase 0 Capability Matrix

**Compiled:** 2026-07-08. All sources accessed 2026-07-08 unless noted.
**Scope:** the candidate models named in CLAUDE.md Section 4, checked against the five
gating columns: (1) assistant-prefill support, (2) full thinking-disable, (3) batch API +
discount, (4) raw vs. summarized reasoning tokens, (5) prompt caching + prices.

**Evidence rules applied (ground rule 1):** every factual cell carries a source URL and
access date. Cells that could not be confirmed from a fetched page are marked
**UNVERIFIED** — they are collected in the "Open questions" section and must be resolved
by the Phase 0 smoke test or further doc fetches, never assumed. Where sources conflict,
the conflict is stated. Source hierarchy: official docs > official pricing pages >
secondary sources (flagged SECONDARY).

**Documentation-domain note:** OpenAI's docs now live at `developers.openai.com/api/docs`
(not `platform.openai.com/docs`); Anthropic's at `platform.claude.com/docs` (the
`docs.anthropic.com` / `docs.claude.com` URLs redirect there).

---

## 0. Headline findings that change Section 4 assumptions

1. **Assistant prefill is nearly extinct among the candidates.** Anthropic's current and
   recent models (Sonnet 4.6, Sonnet 5, Opus 4.6/4.7/4.8, Fable 5) **reject assistant
   prefill with a 400 error** ([migration guide](https://platform.claude.com/docs/en/about-claude/models/migration-guide), 2026-07-08).
   OpenAI's modern models have no prefill mechanism (UNVERIFIED from official docs, which
   are silent; strong secondary evidence). The Gemini API requires the last turn to be
   user-role (secondary evidence of a 400 otherwise). **Only DeepSeek's first-party API
   (Chat Prefix Completion, beta) and self-hosted models support prefill.** The Phase 3
   forced-immediate-answer design must treat the strict-instruction fallback as the
   *primary* mechanism for most of the panel, not the exception.
2. **Claude Sonnet 4.6 is now a legacy model.** Current Anthropic lineup: Claude Fable 5,
   Claude Opus 4.8, Claude Sonnet 5, Claude Haiku 4.5. Sonnet 4.6 is still served
   ([models overview](https://platform.claude.com/docs/en/about-claude/models/overview), 2026-07-08).
3. **Gemini 3 Flash exists but is Preview-only** (`gemini-3-flash-preview`), and its
   thinking floor is `thinking_level: "minimal"`, which per the official Gemini 3 guide
   "does not guarantee that thinking is off." **Gemini 2.5 Flash** is the Gemini model
   with a verified full off-switch (`thinkingBudget: 0`).
4. **DeepSeek-R1-Distill-Qwen-7B has no live serverless endpoint on Together or
   OpenRouter** as of 2026-07-08. The OpenRouter catalog page exists but its endpoints
   API returns `"endpoints": []`; Together's serverless catalog and pricing table omit
   it. The bridge model's Direction 1 runs must be **local** (or a dedicated rented
   endpoint) — the "via Together/OpenRouter" option in Section 4 is currently dead.
5. **`deepseek-chat` and `deepseek-reasoner` are deprecated on 2026-07-24** (16 days from
   compilation). They already map to the non-thinking/thinking modes of
   **`deepseek-v4-flash`**. Pin `deepseek-v4-flash` in configs, not the legacy names.
6. **GPT-5.4 / GPT-5.4-mini exist** but are no longer OpenAI's frontier (GPT-5.5 and the
   GPT-5.6 family have shipped). Both remain fully available and documented.
7. **GLM-5.2 exists** (`glm-5.2`, Z.ai/Zhipu, MIT open weights, ~June 2026) and returns
   **raw** `reasoning_content` — it satisfies the "verify raw thinking tokens" requirement.
8. **Sampling-parameter constraint on current Anthropic models:** Fable 5, Sonnet 5,
   Opus 4.8, Opus 4.7 return 400 on non-default `temperature`/`top_p`/`top_k`
   ([adaptive thinking docs](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking), 2026-07-08).
   The project's "temperature 0" convention **cannot be applied** to those models; Sonnet
   4.6 and Opus 4.6 still accept sampling params. Log this per run config.

---

## 1. Summary table

Prices are USD per 1M tokens (MTok): in / cached-in / out. "Gate" columns are the two
hard gates from Section 4. ✅/❌/⚠️ = verified yes / verified no / qualified (see notes).

| Model (exact ID) | 1. Prefill | 2. Thinking fully off? | 3. Batch API | 4. Raw or summary reasoning | 5. Caching + price (in / cached / out) |
|---|---|---|---|---|---|
| Gemini 3 Flash (`gemini-3-flash-preview`) | ❌ (Gemini-wide; secondary) | ⚠️ NO — floor is `thinking_level:"minimal"`, "does not guarantee off"; docs conflict | ✅ 50% | Summary only (thought summaries + encrypted signatures) | ✅ implicit+explicit; $0.50 / $0.05 / $3.00; cache min 4,096 tok |
| Gemini 2.5 Flash (`gemini-2.5-flash`) — fallback workhorse | ❌ (Gemini-wide; secondary) | ✅ `thinkingConfig.thinkingBudget: 0` | ✅ 50% | Summary only | ✅; $0.30 / $0.03 / $2.50; cache min 2,048 tok |
| GPT-5.4-mini (`gpt-5.4-mini`, snap `gpt-5.4-mini-2026-03-17`) | ❌ effectively (UNVERIFIED from official docs) | ✅ `reasoning_effort:"none"` (default; 0-token claim unverified for mini) | ✅ 50% | Summary only; raw never exposed (encrypted round-trip only) | ✅ (90% off cached); $0.75 / $0.075 / $4.50; batch $0.375 / $0.0375 / $2.25 |
| GPT-5.4 (`gpt-5.4`, snap `gpt-5.4-2026-03-05`) | ❌ effectively (UNVERIFIED from official docs) | ✅ `reasoning_effort:"none"` (default; 0 reasoning tokens — secondary) | ✅ 50% | Summary only; raw never exposed | ✅; $2.50 / $0.25 / $15.00; batch $1.25 / $0.13 / $7.50 |
| Claude Sonnet 4.6 (`claude-sonnet-4-6`) — **legacy** | ❌ 400 error | ✅ off by default; `thinking:{type:"disabled"}` | ✅ 50% ($1.50/$7.50 batch) | Summary only (`display` default `"summarized"`); raw never returned | ✅; $3 / $0.30 read ($3.75 5m-write) / $15 |
| Claude Sonnet 5 (`claude-sonnet-5`) — current mid-tier | ❌ 400 error | ✅ but adaptive thinking is ON by default — must set `thinking:{type:"disabled"}` | ✅ 50% | Summary only (`display` default `"omitted"`) | ✅; intro to 2026-08-31: $2 / $0.20 read / $10; then $3 / $0.30 / $15 |
| Claude Opus 4.8 (`claude-opus-4-8`) — current frontier | ❌ 400 error | ✅ off unless `thinking:{type:"adaptive"}` set | ✅ 50% ($2.50/$12.50 batch) | Summary only | ✅; $5 / $0.50 read / $25 |
| Claude Fable 5 (`claude-fable-5`) | ❌ 400 error | ❌ CANNOT disable (adaptive always on) → **separate stratum** | ✅ 50% ($5/$25 batch) | Summary only; raw never returned | ✅; $10 / $1 read / $50 |
| Claude Haiku 4.5 (`claude-haiku-4-5`) | UNVERIFIED | ✅ extended thinking off unless enabled | ✅ 50% ($0.50/$2.50 batch) | Summary only | ✅; $1 / $0.10 read / $5 |
| DeepSeek V4 Flash (`deepseek-v4-flash`) — replaces `deepseek-chat`/`deepseek-reasoner` on 2026-07-24 | ✅ Chat Prefix Completion (**beta** base URL, `prefix:true`) | ✅ non-thinking mode on same checkpoint | UNVERIFIED (no batch/off-peak on current pricing pages) | **RAW** — `reasoning_content` is full CoT (thinking mode) | ✅ auto cache; $0.14 miss / $0.0028 hit / $0.28 out |
| DeepSeek V4 Pro (`deepseek-v4-pro`) | ✅ same beta mechanism (doc example uses this model) | ✅ both modes | UNVERIFIED | **RAW** `reasoning_content` | ✅; $0.435 miss / $0.003625 hit / $0.87 out |
| GLM-5.2 (Z.ai `glm-5.2`; OpenRouter `z-ai/glm-5.2`) | UNVERIFIED | ✅ `thinking:{type:"disabled"}` | ❌ on OpenRouter (router); Z.ai first-party UNVERIFIED | **RAW** — `reasoning_content` in stream | OpenRouter from ~$0.93 / — / $3.00 (DeepInfra); Z.AI/Together tier $1.40/$4.40; caching UNVERIFIED |
| DeepSeek-R1-Distill-Qwen-7B (HF `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`) — **bridge** | N/A hosted — **no live serverless endpoint**; ✅ trivially when self-hosted | ❌ reasoning-trained, no native off switch (expected: `<think>` always) | N/A | **RAW** `<think>` tokens when run locally | No serverless price exists on Together/OpenRouter (2026-07-08); run locally |

---

## 2. Per-model notes and sources

### 2.1 Google — Gemini 3 Flash (`gemini-3-flash-preview`) and lineup

**Existence/status:** Real, but **Preview**, not GA. Newer siblings: Gemini 3.5 Flash
(`gemini-3.5-flash`, GA, backs `gemini-flash-latest`, ~3x the price) and Gemini 3.1
Flash-Lite (`gemini-3.1-flash-lite`, GA, cheaper). Models page last updated 2026-06-30.
Source: https://ai.google.dev/gemini-api/docs/models ; https://ai.google.dev/gemini-api/docs/models/gemini-3-flash-preview (2026-07-08).

1. **Prefill: NO.** `generateContent` requires the final turn to be user-role/roleless; a
   trailing `role:"model"` turn is rejected (HTTP 400: "Please ensure that single turn
   requests end with a user role or the role field is empty."). Official docs are silent
   on prefill (no such feature documented); the explicit 400 evidence is **SECONDARY**
   (https://github.com/microsoft/semantic-kernel/issues/13262, 2026-07-08). Official-doc
   silence + secondary 400: treat as NO, confirm in smoke test.
2. **Thinking disable: NOT PROVABLE (conflict).** Gemini 3 models use `thinking_level`
   (`minimal|low|medium|high`) — there is **no `thinkingBudget: 0`** for Gemini 3. The
   official Gemini 3 guide states "minimal does not guarantee that thinking is off"
   (https://ai.google.dev/gemini-api/docs/gemini-3, 2026-07-08). **CONFLICT:** the
   thinking docs table (https://ai.google.dev/gemini-api/docs/thinking, 2026-07-08) marks
   Gemini 3 Flash "Can disable? YES" via `minimal`. The explicit developer-guide sentence
   is weighted as authoritative → per the hidden-reasoning policy, Gemini 3 Flash belongs
   in the **cannot-fully-disable stratum** unless an empirical test shows 0 thinking
   tokens at `minimal`.
   Lineup: Gemini 2.5 Flash **can** fully disable (`thinkingBudget: 0`); 2.5 Flash-Lite
   thinking off by default; 2.5 Pro cannot disable; 3.x Pro cannot (floor `low`).
   Source: https://ai.google.dev/gemini-api/docs/thinking (2026-07-08).
3. **Batch: YES, 50%** ("Batch API (50% cost reduction)"), up to 24h turnaround; caching
   works inside batch jobs. Sources: https://ai.google.dev/gemini-api/docs/pricing ;
   https://ai.google.dev/gemini-api/docs/batch-api (2026-07-08).
4. **Raw vs summary: SUMMARY ONLY.** The API returns opt-in *thought summaries* plus
   opaque encrypted *thought signatures*; raw thinking tokens are never exposed on any
   Gemini model. Sources: https://ai.google.dev/gemini-api/docs/thinking ;
   https://ai.google.dev/gemini-api/docs/gemini-3 (2026-07-08).
5. **Caching + prices (per MTok, paid tier):** input $0.50 (audio $1.00), output $3.00,
   cached input $0.05, cache storage $1.00/MTok/hr. Implicit caching automatic on 2.5+
   models; explicit `CachedContent` also available (not in the Interactions API).
   **Minimum tokens to cache: 4,096 for Gemini 3.x** (2,048 for 2.5) — note the project's
   ~2–2.5K-token shared stems may fall *under* the 3.x threshold and never cache, but
   would cache on 2.5 Flash. Batch price line-item not separately displayed; derived
   ≈ $0.25/$1.50 (**UNVERIFIED — derived** from the global 50% statement). Sources:
   https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/docs/caching (2026-07-08).

**Gemini 2.5 Flash (`gemini-2.5-flash`)** — fallback workhorse: input $0.30 (audio
$1.00), output $2.50, cached $0.03; batch 50%; thinking fully disableable
(`thinkingBudget: 0`); same no-prefill and summary-only constraints.
Sources: https://ai.google.dev/gemini-api/docs/pricing ; https://ai.google.dev/gemini-api/docs/thinking (2026-07-08).

### 2.2 OpenAI — GPT-5.4 (`gpt-5.4`) and GPT-5.4-mini (`gpt-5.4-mini`)

**Existence/status:** Both exist with official model pages; superseded as flagship by
GPT-5.5 ($5/$30) and the GPT-5.6 family, but fully available. Snapshots:
`gpt-5.4-2026-03-05`, `gpt-5.4-mini-2026-03-17`. GPT-5.4: 1,050,000-token context,
128K max output (inputs >272K priced 2x in / 1.5x out). GPT-5.4-mini: 400K context,
128K max output. Sources: https://developers.openai.com/api/docs/models/gpt-5.4 ;
https://developers.openai.com/api/docs/models/gpt-5.4-mini ;
https://developers.openai.com/api/docs/guides/latest-model (2026-07-08).

1. **Prefill: effectively NO — UNVERIFIED from official docs.** Official docs are silent
   on any prefill/continuation mechanism for Chat Completions or Responses. SECONDARY
   evidence: response prefixing "isn't supported with newer models that use ChatML"
   (legacy Completions endpoint only) and prefill is unsupported for OpenAI reasoning
   models (https://community.openai.com/t/openai-raw-prompting-response-prefixing/972002,
   2026-07-08). Use the strict-instruction fallback; confirm empirically in smoke test.
2. **Thinking disable: YES.** `reasoning_effort` (Responses: `reasoning.effort`) takes
   `none|low|medium|high|xhigh`; `none` is listed as the **default** on both 5.4 model
   pages and "behaves like a non-thinking model." Note: no `minimal` value on 5.4 (that
   was GPT-5.0-era). 0-reasoning-token behavior at `none` confirmed for `gpt-5.4` via
   SECONDARY community thread; **UNVERIFIED for `gpt-5.4-mini`** specifically. **Known
   bug (SECONDARY, may be fixed):** passing `max_completion_tokens` alongside
   `reasoning_effort:"none"` can silently re-enable reasoning
   (https://community.openai.com/t/gpt-5-4-ignores-reasoning-effort-none-when-max-completion-tokens-is-used/1378362, 2026-07-08)
   — must be checked in the smoke test since forced-answer calls will cap output tokens.
   Also: default effort differs across generations (5.5 defaults to `medium`) — always
   set `reasoning_effort` explicitly and log it (ground rule 4).
   Sources: model pages above; https://developers.openai.com/api/docs/guides/reasoning (2026-07-08).
3. **Batch: YES, 50%.** Source: https://developers.openai.com/api/docs/pricing (2026-07-08).
4. **Raw vs summary: SUMMARY ONLY.** "Reasoning tokens are not visible via the API"
   (billed as output); optional summaries via `summary` (`auto|concise|detailed`); raw
   CoT round-trips only encrypted (`reasoning.encrypted_content`), never human-readable.
   Source: https://developers.openai.com/api/docs/guides/reasoning (2026-07-08).
5. **Caching + prices (per MTok):** caching supported, cached input = 10% of input (90%
   discount).
   - `gpt-5.4`: $2.50 in / $0.25 cached / $15.00 out; batch $1.25 / $0.13 / $7.50.
   - `gpt-5.4-mini`: $0.75 in / $0.075 cached / $4.50 out; batch $0.375 / $0.0375 / $2.25.
   Sources: https://developers.openai.com/api/docs/pricing + both model pages (2026-07-08);
   pricing page and model pages agree (no conflict).

### 2.3 Anthropic — Claude Sonnet 4.6 and current lineup

**Existence/status:** `claude-sonnet-4-6` still served but listed under **"Legacy
models"** ("consider migrating"). Current comparison-table lineup: **Claude Fable 5
(`claude-fable-5`), Claude Opus 4.8 (`claude-opus-4-8`), Claude Sonnet 5
(`claude-sonnet-5`), Claude Haiku 4.5 (`claude-haiku-4-5` / dated
`claude-haiku-4-5-20251001`)**. Closest current equivalent of "Sonnet 4.6 as frontier":
Sonnet 5 (mid-tier) or Opus 4.8 (top, Fable 5 highest). Researcher must choose; not
substituted here. Source: https://platform.claude.com/docs/en/about-claude/models/overview (2026-07-08).

1. **Prefill: NO — 400 error, all current/recent models.** Migration guide (2026-07-08):
   "Prefilling the assistant message returns a `400` error on Claude Sonnet 5, the same
   as on Claude Sonnet 4.6"; same for Opus 4.7; "Prefilling the assistant message is not
   supported on `claude-fable-5` and returns a 400 error, the same as on Claude Opus
   4.8. Use system prompt instructions instead." This is a blanket rejection, not merely
   the old thinking-on restriction. Haiku 4.5 not enumerated → UNVERIFIED for Haiku.
   Source: https://platform.claude.com/docs/en/about-claude/models/migration-guide (2026-07-08).
2. **Thinking disable:** per model (source: https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking, 2026-07-08):
   - Sonnet 4.6: adaptive thinking **off by default**; `thinking:{type:"disabled"}`
     supported; manual `{type:"enabled",budget_tokens:N}` accepted but deprecated. ✅
   - Sonnet 5: adaptive **ON by default** — must explicitly pass
     `thinking:{type:"disabled"}` (supported). Manual enabled → 400. ✅ (explicit)
   - Opus 4.8 (and 4.7): off unless `{type:"adaptive"}` set; `disabled` accepted. ✅
   - **Fable 5: CANNOT disable** — "adaptive thinking is always on;
     `thinking:{type:"disabled"}` is not supported." → hidden-reasoning **separate
     stratum** if paneled. ❌
   - Haiku 4.5: extended (manual) thinking, off unless enabled. ✅
3. **Batch: YES, 50%** on input+output (Message Batches API). Batch prices per MTok:
   Sonnet 4.6 $1.50/$7.50; Sonnet 5 intro $1/$5 (through 2026-08-31) then $1.50/$7.50;
   Opus 4.8 $2.50/$12.50; Fable 5 $5/$25; Haiku 4.5 $0.50/$2.50. The ≤24h turnaround SLA
   was not restated on the fetched pages (**UNVERIFIED** here). Optional 300K-output
   batch beta (`output-300k-2026-03-24`) on Opus 4.6–4.8/Sonnet 5/Sonnet 4.6.
   Sources: https://platform.claude.com/docs/en/about-claude/pricing ;
   https://platform.claude.com/docs/en/about-claude/models/overview (2026-07-08).
4. **Raw vs summary: SUMMARY ONLY, never raw**, on all Claude 4/5 models. `display`
   controls output: defaults `"summarized"` on Sonnet 4.6/Opus 4.6/Haiku 4.5-era, but
   **`"omitted"`** on Sonnet 5/Opus 4.7/4.8/Fable 5 (must request `"summarized"`).
   Sources: https://platform.claude.com/docs/en/build-with-claude/extended-thinking ;
   adaptive-thinking doc (2026-07-08).
5. **Caching + prices (per MTok: in / 5m-write / 1h-write / read / out)** — source:
   https://platform.claude.com/docs/en/about-claude/pricing (2026-07-08):
   - Sonnet 4.6: $3 / $3.75 / $6 / $0.30 / $15. Context 1M, max out 128K.
   - Sonnet 5: **intro through 2026-08-31:** $2 / $2.50 / $4 / $0.20 / $10; from
     2026-09-01: $3 / $3.75 / $6 / $0.30 / $15. Newer tokenizer ≈ +30% tokens for the
     same text vs Sonnet 4.6 — factor into `src/costs.py`.
   - Opus 4.8: $5 / $6.25 / $10 / $0.50 / $25.
   - Fable 5: $10 / $12.50 / $20 / $1 / $50.
   - Haiku 4.5: $1 / $1.25 / $2 / $0.10 / $5. Context 200K, max out 64K.

**Sampling caveat (affects the harness):** Fable 5, Sonnet 5, Opus 4.8, Opus 4.7 return
400 on non-default `temperature`/`top_p`/`top_k`; Sonnet 4.6 and Opus 4.6 accept them.
The project's temperature-0 convention only works on the legacy pair; on current models,
runs execute at provider defaults and the config must record that. Source:
https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking (2026-07-08).

### 2.4 DeepSeek — `deepseek-v4-flash` (replaces `deepseek-chat` / `deepseek-reasoner`)

**Existence/status:** **`deepseek-chat` and `deepseek-reasoner` are deprecated on
2026-07-24 15:59 UTC.** Official pricing page: "For compatibility, they correspond to the
non-thinking mode and thinking mode of `deepseek-v4-flash`, respectively." Also
available: `deepseek-v4-pro`. Configs must pin `deepseek-v4-flash` (or `-pro`) directly.
Source: https://api-docs.deepseek.com/quick_start/pricing (2026-07-08).

1. **Prefill: YES (beta).** "Chat Prefix Completion": set
   `base_url="https://api.deepseek.com/beta"` and end messages with
   `{"role":"assistant", "content":"...", "prefix": true}` — the model continues from
   the supplied assistant text. Official doc example uses `deepseek-v4-pro`. Interaction
   with thinking mode on v4-flash is **UNVERIFIED** (doc does not address it) — must be
   smoke-tested. Source: https://api-docs.deepseek.com/guides/chat_prefix_completion (2026-07-08).
2. **Thinking disable: YES.** `deepseek-v4-flash` "supports thinking and non-thinking
   modes" on the same checkpoint; select non-thinking mode. (Legacy `deepseek-reasoner`
   had no toggle — you switched models.) Exact request parameter for mode selection
   **UNVERIFIED** from fetched pages (legacy names selected the mode implicitly; confirm
   the parameter in the API reference / smoke test).
   Sources: https://api-docs.deepseek.com/quick_start/pricing ;
   https://api-docs.deepseek.com/guides/reasoning_model (2026-07-08).
3. **Batch: UNVERIFIED / apparently none.** No batch API or off-peak discount appears on
   the current official pricing pages. (Historical 50–75% off-peak window 16:30–00:30
   UTC for V3/R1 per SECONDARY sources; not confirmed for V4.) Sources:
   https://api-docs.deepseek.com/quick_start/pricing ;
   https://api-docs.deepseek.com/quick_start/pricing-details-usd/ (2026-07-08).
4. **Raw vs summary: RAW.** In thinking mode the API returns `reasoning_content` — the
   "raw, complete chain-of-thought, not a summary" — alongside `content`; `max_tokens`
   includes the CoT. This is the only *API-served panel candidate* with verified raw
   native traces. Source: https://api-docs.deepseek.com/guides/reasoning_model (2026-07-08).
5. **Caching + prices (per MTok):** automatic context caching, on by default.
   - `deepseek-v4-flash`: **$0.14 input (cache miss) / $0.0028 (cache hit) / $0.28
     output**; 1M context; 384K max output.
   - `deepseek-v4-pro`: $0.435 miss / $0.003625 hit / $0.87 out. (A SECONDARY source,
     tokenmix, claims the launch 75% discount is now permanent; official page just lists
     these numbers.)
   - Legacy (until 2026-07-24): `deepseek-chat` $0.27/$0.07/$1.10; `deepseek-reasoner`
     $0.55/$0.14/$2.19 (64K context).
   Sources: https://api-docs.deepseek.com/quick_start/pricing ;
   https://api-docs.deepseek.com/quick_start/pricing-details-usd/ (2026-07-08).

### 2.5 GLM-5.2 (Zhipu/Z.ai) — `glm-5.2`

**Existence/status:** Exists. MIT-licensed open weights; released mid-June 2026 (minor
date conflict: June 13 per SECONDARY roundups vs "Released Jun 16, 2026" on OpenRouter,
slug variant `z-ai/glm-5.2-20260616`). 1M context on Z.ai. CLAUDE.md already excludes it
from probing (744B MoE). Sources: https://docs.z.ai/guides/llm/glm-5.2 ;
https://openrouter.ai/z-ai/glm-5.2 (2026-07-08).

1. **Prefill: UNVERIFIED.** Not mentioned in the Z.ai doc; no prefill/assistant-
   continuation parameter in OpenRouter endpoint metadata. Needs a live test.
   Sources: https://docs.z.ai/guides/llm/glm-5.2 ;
   https://openrouter.ai/api/v1/models/z-ai/glm-5.2/endpoints (2026-07-08).
2. **Thinking disable: YES.** Official toggle `"thinking": {"type": "enabled"|"disabled"}`,
   plus `reasoning_effort` (`high`/`max`; OpenRouter adds `xhigh`).
   Source: https://docs.z.ai/guides/llm/glm-5.2 (2026-07-08).
3. **Batch:** OpenRouter has no batch API (router). Z.ai first-party batch: UNVERIFIED.
4. **Raw vs summary: RAW.** Streaming responses expose
   `chunk.choices[0].delta.reasoning_content` directly (official example) — raw thinking
   text, satisfying Section 4's "verify raw thinking tokens" requirement (pending a live
   confirmation that hosted providers pass it through unmodified).
   Source: https://docs.z.ai/guides/llm/glm-5.2 (2026-07-08).
5. **Prices/IDs:** OpenRouter `z-ai/glm-5.2`, ~28 providers; cheapest DeepInfra
   **$0.93 in / $3.00 out**; Z.AI first-party, Together, Fireworks, Cloudflare cluster at
   **$1.40 / $4.40**; all endpoints report `reasoning` + `include_reasoning` support.
   Max-output conflict: Z.ai doc says 128K; OpenRouter default card says 32K (varies by
   provider). Prompt-caching prices: UNVERIFIED. Together-direct serverless ID/price:
   UNVERIFIED (page exists, not fetched). Sources: https://openrouter.ai/z-ai/glm-5.2 ;
   https://openrouter.ai/api/v1/models/z-ai/glm-5.2/endpoints ;
   https://docs.z.ai/guides/llm/glm-5.2 (2026-07-08).

### 2.6 DeepSeek-R1-Distill-Qwen-7B — the bridge model (highest-stakes cell)

**Hosted availability: NONE (serverless), as of 2026-07-08.**
- **OpenRouter:** catalog page exists (`deepseek/deepseek-r1-distill-qwen-7b`, 131K
  context) but the endpoints API returns **`"endpoints": []`** — zero live providers;
  not callable. Sources: https://openrouter.ai/deepseek/deepseek-r1-distill-qwen-7b ;
  https://openrouter.ai/api/v1/models/deepseek/deepseek-r1-distill-qwen-7b/endpoints (2026-07-08).
- **Together AI:** absent from the serverless catalog and the serverless pricing table
  (R1 distills appear only as fine-tuning bases); the direct 7B model page 404s. A 14B
  distill page exists but advertises dedicated/reserved deployment, not serverless.
  Sources: https://docs.together.ai/docs/serverless-models ; https://www.together.ai/models ;
  https://www.together.ai/pricing ; https://www.together.ai/models/deepseek-r1-distilled-qwen-14 (2026-07-08).

**Consequence:** the Section 4 note "(local, or via Together/OpenRouter)" is now
**local-only** (or rent a dedicated endpoint). Since Direction 2 already requires local
generation (HF/vLLM) on the same checkpoint, the cleanest path is to run its Direction 1
behavioral conditions through the same local stack — which also gives free prefill and
raw `<think>` traces. Possible hosted alternates surfaced but unverified: NVIDIA NIM
(`build.nvidia.com/deepseek-ai/deepseek-r1-distill-qwen-7b`), Featherless (SECONDARY,
prices unconfirmed).

Columns, for the local path: prefill ✅ (trivial — you control the template);
thinking-disable ❌ (reasoning-trained, no native off switch; suppression via
prompt/prefill is imperfect → the model sits in the cannot-disable stratum by
construction, which is acceptable since its raw `<think>` text *is* the studied CoT);
batch N/A; reasoning **raw `<think>` tokens** (expected from the distill family;
confirm on first local generation); price = GPU time, not per-token. Canonical HF ID:
`deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`.

---

## 3. Workhorse recommendation (for researcher approval — not a decision)

**Hard gates (Section 4):** (a) assistant-prefill support, (b) full thinking-disable.

| Candidate | Prefill gate | Thinking-off gate | Verdict |
|---|---|---|---|
| Gemini 3 Flash | ❌ | ⚠️ not provable (`minimal` floor) | fails both |
| GPT-5.4-mini | ❌ (effectively) | ✅ `reasoning_effort:"none"` | fails prefill |
| Gemini 2.5 Flash | ❌ | ✅ `thinkingBudget: 0` | fails prefill |
| **DeepSeek V4 Flash** | ✅ (beta prefix completion) | ✅ (non-thinking mode) | **passes both** |

**Recommendation: adopt `deepseek-v4-flash` (non-thinking mode) as the workhorse,**
pending two smoke-test confirmations (prefill beta actually works; non-thinking mode
emits zero reasoning). Reasoning:
- It is the **only API candidate that passes both hard gates** — neither of CLAUDE.md's
  named workhorse candidates (Gemini 3 Flash, GPT-5.4-mini) supports prefill, and
  Gemini 3 Flash also fails the thinking-off gate.
- It is by far the cheapest ($0.14/$0.28 per MTok, cache hits at $0.0028), so the lack
  of a verified batch API costs little in absolute terms; automatic caching directly
  benefits the stem-first prompt structure.
- Bonus: its thinking mode returns **raw** `reasoning_content`, giving the workhorse a
  native-trace capability none of the Western APIs offer.
- Risks to weigh: prefix completion is a **beta** endpoint (stability unverified);
  prefill × thinking-mode interaction undocumented; no verified batch discount; and a
  serving-stack change on 2026-07-24 (legacy-name sunset) — pin `deepseek-v4-flash` and
  re-run the nondeterminism control after that date if generation spans it.

**Fallback if the researcher wants a Western-hosted workhorse or DeepSeek's beta prefill
fails the smoke test:** **Gemini 2.5 Flash** (`gemini-2.5-flash`) — verified full
thinking-off, 50% batch, cheap ($0.30/$2.50), 2,048-token cache floor that the ~2–2.5K
stems can clear; accept instruction-only forced answers. **GPT-5.4-mini** is the
alternative on the same terms ($0.75/$4.50, 90% cache discount, `none` default effort).

**Design-level implication the researcher must sign off on:** since only DeepSeek (and
the self-hosted bridge model) support prefill, the Phase 3 forced-immediate-answer
mechanism will run as **strict instruction-only for most of the panel** (Anthropic 400s
prefill; OpenAI and Gemini lack it). The Decisions-log entry of 2026-07-07 ("prefill
where supported") stands, but "where supported" is now a small minority — worth
recording explicitly, and worth adding the prefill-vs-instruction mechanism as an
analysis covariate since it varies systematically across providers.

**Panel notes (same gates):**
- Closed frontier: "Claude Sonnet 4.6" is legacy; researcher should choose among
  Sonnet 4.6 (still served; accepts temperature 0), Sonnet 5 / Opus 4.8 (current;
  thinking disableable but **temperature is locked to default**), or GPT-5.4 (thinking
  off via `none`). Claude Fable 5 cannot disable thinking → separate stratum if included.
- Open-weight reasoning: GLM-5.2 verified with raw `reasoning_content` and a thinking
  toggle — satisfies the Section 4 raw-trace requirement, pending a live pass-through
  check on the chosen provider (OpenRouter multi-provider routing should be pinned to
  one provider for reproducibility).
- Bridge model: local-only (Section 2.6); plan GPU time accordingly.

---

## 4. Open questions for the researcher (all UNVERIFIED cells + required live tests)

**UNVERIFIED cells (could not be confirmed from any fetched page):**
1. OpenAI prefill absence for `gpt-5.4`/`gpt-5.4-mini` — official docs silent; conclusion
   rests on SECONDARY community evidence.
2. `reasoning_effort:"none"` → 0 reasoning tokens for **`gpt-5.4-mini`** specifically
   (verified only for `gpt-5.4`, and via a SECONDARY thread).
3. The `max_completion_tokens` + `reasoning_effort:"none"` re-enable bug (SECONDARY; may
   be fixed) — directly threatens forced-answer calls.
4. Prefill status for **Claude Haiku 4.5** (not enumerated in the migration guide).
5. Anthropic Batch **24h turnaround SLA** (not on fetched pages).
6. DeepSeek **batch/off-peak discount** for V4 models (nothing on official pages).
7. DeepSeek V4 Flash: the exact **parameter that selects non-thinking mode** post-sunset,
   and the **prefill × thinking interaction**.
8. GLM-5.2 **assistant-prefill support** (not documented anywhere fetched).
9. GLM-5.2 **prompt-caching prices** and Z.ai first-party **batch API**.
10. GLM-5.2 Together-direct serverless ID/price; max-output conflict (Z.ai 128K vs
    OpenRouter card 32K); release-date conflict (Jun 13 vs Jun 16, 2026).
11. Gemini 3 Flash **batch line-item price** (derived ≈$0.25/$1.50 from the global 50%
    statement, not shown per-line).
12. Gemini per-model **context windows** beyond `gemini-3-flash-preview` (1,048,576 in /
    65,536 out verified for it only).
13. R1-Distill-Qwen-7B on secondary hosts (NVIDIA NIM, Featherless) — existence surfaced,
    pricing/liveness unverified.
14. Anthropic Bedrock model-ID suffixes (markdown capture ambiguity); first-party IDs
    are clean.

**Documented conflicts:**
- Gemini 3 Flash thinking-disable: thinking-docs table says "YES via minimal" vs Gemini 3
  developer guide "minimal does not guarantee that thinking is off." Resolved here in
  favor of the developer guide; empirical test required.
- Sonnet 4.6 max output: official 128K vs SECONDARY aggregators' 64K (official wins).
- GLM-5.2 max output and release date (above).

**Live API tests needed in the Phase 0 smoke test (out of scope for this document):**
- DeepSeek beta Chat Prefix Completion actually continues from the prefix on
  `deepseek-v4-flash`, in both thinking and non-thinking modes.
- DeepSeek non-thinking mode emits zero `reasoning_content`.
- OpenAI: confirm no prefill path exists (Chat Completions + Responses), and that
  `reasoning_effort:"none"` yields 0 reasoning tokens **with** an output-token cap set.
- Gemini: confirm trailing model-role turn is rejected (prefill impossible), and measure
  `thoughts_token_count` at `thinking_level:"minimal"` on `gemini-3-flash-preview` vs
  `thinkingBudget:0` on `gemini-2.5-flash`.
- Anthropic: confirm the 400-on-prefill on the chosen panel model, and confirm
  temperature-0 acceptance (Sonnet 4.6/Opus 4.6 only per docs).
- GLM-5.2: test prefill; verify the chosen provider passes through raw
  `reasoning_content` unmodified (pin one OpenRouter provider or use Z.ai direct).
- R1-Distill-Qwen-7B: confirm local generation emits raw `<think>` traces; re-check
  OpenRouter endpoints (a provider could reappear) before committing to local-only.

**Decisions needing researcher approval (per ground rule 3):**
- Workhorse choice (recommendation above).
- Replacement for "Claude Sonnet 4.6" as closed frontier (legacy vs Sonnet 5/Opus 4.8),
  including how to handle the temperature-lock on current Anthropic models.
- Whether Phase 3's forced-answer mechanism heterogeneity (prefill vs instruction-only,
  now provider-correlated) is acceptable or should be homogenized (instruction-only
  everywhere, prefill as a DeepSeek/local-only robustness check).
- Whether to pin `deepseek-v4-flash` now given the 2026-07-24 legacy-name sunset.
