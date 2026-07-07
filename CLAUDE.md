# CLAUDE.md — CoT Faithfulness Research Project

You are assisting with an AI safety research project run by a pre-college researcher
(strong math background: AIME/USAJMO, linear algebra, calculus; comfortable in Python;
new to PyTorch/TransformerLens). Your job is to help build, run, and analyze the
experiments described below. The researcher makes all scientific judgment calls;
you handle engineering, execution, and first-pass analysis.

---

## 1. Project overview

**Research question:** How faithful is chain-of-thought (CoT) reasoning on open-ended
generative tasks (math word problems, ethical dilemmas, logic puzzles, multi-step
factual questions), and does faithfulness vary systematically by task type and model
capability?

**Positioning (2026):** Turpin et al. (2023), Lanham et al. (2023), and Chen et al.
(2025, arXiv:2505.05410) established faithfulness failures mainly in multiple-choice /
hint paradigms. The gap we target: **open-ended generative tasks**, measured with two
complementary behavioral methods, plus (stretch goal) activation probing on the same
small open model so behavioral and internal evidence can be compared directly.

**Two directions:**
- **Direction 1 (primary, behavioral):** CoT step corruption + hint paradigm, run via
  APIs across a model panel.
- **Direction 2 (stretch, interpretability):** linear probes on residual-stream
  activations of one small open reasoning model, compared against its CoT text.

---

## 2. Ground rules for Claude Code

1. **Never fabricate experimental results.** If a run fails or data is missing, say so.
   All reported numbers must come from actual pipeline outputs on disk.
2. **Cost discipline.** Before any batch of API calls, print an estimated cost
   (tokens × current price) and ask for confirmation if the batch exceeds **$5**.
   Maintain a running cost log in `logs/cost_log.csv`. Hard project budget: **$450 API**.
3. **Human-in-the-loop for science.** Do not silently change the experimental design,
   corruption taxonomy, task selection criteria, or statistical analysis plan. Propose
   changes; the researcher approves.
4. **Reproducibility.** Every experiment run gets a config file (model, temperature,
   prompt template, seed, date) saved alongside its outputs. Pin dependency versions.
5. **No cherry-picking.** Analysis scripts report all conditions, including null and
   negative results. Pre-registered metrics (Section 6) are computed exactly as
   specified before any exploratory analysis.
6. **Small before big.** Every pipeline is validated on 5 problems before running on
   50, and on one model before running the panel.
7. **Secrets:** API keys live in `.env` (gitignored). Never print or commit keys.

---

## 3. Repository structure

```
cot-faithfulness/
├── CLAUDE.md                  # this file
├── .env                       # API keys (gitignored)
├── configs/                   # one YAML per experiment run
├── data/
│   ├── raw/                   # source datasets (GSM8K subset, etc.)
│   ├── tasks/                 # curated 200-problem task set (4 types × 50)
│   └── results/               # raw API responses, one JSONL per run
├── src/
│   ├── generation.py          # prompt model, collect CoT answers
│   ├── parsing.py             # split free-form CoT into discrete steps
│   ├── corruption.py          # apply corruption taxonomy to steps
│   ├── hints.py               # hint-paradigm cue injection + admission check
│   ├── evaluation.py          # answer extraction, correctness, change detection
│   ├── probing/               # Direction 2: activation extraction, linear probes
│   └── analysis/              # metrics, stats, plots
├── notebooks/                 # exploratory analysis only, never canonical results
├── logs/                      # cost log, run logs
└── writeup/                   # paper/blog draft, figures
```

---

## 4. Model configuration

### Direction 1 panel (behavioral, API)

| Role | Model | Notes |
|---|---|---|
| Cheap workhorse (development + bulk) | Gemini 3 Flash or GPT-5.4-mini | Use for all pipeline debugging |
| Open-weight reasoning | GLM-5.2 (via OpenRouter/Together) and/or DeepSeek | Verify the API returns **raw** thinking tokens, not a summary, before relying on native traces |
| Closed frontier | Claude Sonnet 4.6 and/or GPT-5.4 | Capability-scaling comparison |

- Default approach: **prompted CoT** ("think step by step") so the reasoning is plain
  text we fully control, regardless of whether a model hides native reasoning traces.
- Use batch APIs where available (~50% discount) and prompt caching (corruption
  variants share the same problem stem — structure prompts stem-first to exploit this).
- Temperature 0 for generation runs unless the config says otherwise; log it always.

### Direction 2 model (probing, local/GPU)

- One small **dense** open reasoning model: **DeepSeek-R1-Distill-Qwen-7B**,
  **R1-Distill-Llama-8B**, or **OLMo-3-7B-Think**.
- **First task before any probing work:** verify the exact checkpoint loads in
  TransformerLens with residual-stream hooks. If it does not, report the failure and
  the researcher will pick a fallback — do not silently substitute a model.
- GLM-5.2 is **out of scope** for probing (744B MoE; unsupported and unaffordable).
- Environment: single GPU (Colab Pro+ or rented ~24GB card). Write probing code to
  checkpoint activations to disk so interrupted sessions resume cleanly.

### Claude Code model note

Run this project with the strongest available model for design/analysis sessions and a
cheaper model for routine code edits. Check what's available with `/model` — model
lineups and access change. Long experiment runs should be plain Python scripts launched
in the shell (nohup / tmux), **not** kept alive inside an interactive session.

---

## 5. Phased task list

Work through phases in order. Each phase ends with a checkpoint the researcher reviews.

### Phase 0 — Setup (week 1)
- [ ] Initialize repo, venv, pinned `requirements.txt`, `.env` template, cost logger.
- [ ] Write `src/generation.py` with a provider-agnostic client (OpenAI, Anthropic,
      Google, OpenRouter) and retry/backoff.
- [ ] Smoke test: 5 problems through the workhorse model end-to-end; verify JSONL
      output schema.
- **Checkpoint:** researcher reviews output schema and cost log.

### Phase 1 — Task set (weeks 1–2)
- [ ] Build `data/tasks/`: 4 task types × 50 problems.
      - Math word problems: sample from GSM8K (or similar), stratified by difficulty.
      - Logic puzzles: multi-step deductive puzzles with unique answers.
      - Multi-step factual: questions requiring 2+ hops, with ground truth.
      - Ethical dilemmas: short scenarios; since "correct answer" is contested, the
        measured outcome is **answer change**, not correctness. Flag this clearly in
        analysis code.
- [ ] Each problem gets: id, type, source, ground truth (where applicable), license.
- [ ] **Baseline replication:** reproduce one published faithfulness number (e.g., a
      Chen et al. hint-paradigm condition) on a comparable setup to validate our
      harness. If our number is wildly off, stop and debug before proceeding.
- **Checkpoint:** researcher signs off on task set and baseline replication.

### Phase 2 — CoT parsing (weeks 2–3, highest technical risk)
- [ ] `src/parsing.py`: split free-form CoT into discrete steps. Strategy:
      1. Prompt models to number their steps explicitly (cheap, do this first).
      2. Fallback: sentence/paragraph segmentation + an LLM-based step splitter.
- [ ] Validation: on 30 hand-labeled CoTs (researcher labels), parser must reach ≥90%
      step-boundary agreement. Report the number honestly.
- **Checkpoint:** parser validation report.

### Phase 3 — Direction 1, Method A: step corruption (weeks 3–5)
- [ ] `src/corruption.py` implementing the taxonomy:
      - **Subtle:** small numerical/entity error in one step.
      - **Reversal:** flip an intermediate conclusion.
      - **Omission:** delete a step.
      Vary corrupted position: early / middle / late.
- [ ] Re-injection prompt template: present corrupted CoT as the model's own prior
      reasoning, ask for the final answer.
- [ ] **Noticeability control:** show each corrupted CoT to a second model as "a
      student's reasoning — is it correct?" Record detection rate per corruption type.
- [ ] Run: 5-problem dry run → full 200 problems on workhorse model → panel.
- **Checkpoint:** researcher reviews per-condition sample transcripts before panel run.

### Phase 4 — Direction 1, Method B: hint paradigm (weeks 5–6)
- [ ] `src/hints.py`: inject biasing cues (suggested answer, authority cue, answer
      reordering where applicable) into prompts.
- [ ] Measure: (a) did the answer shift toward the cue? (b) does the CoT mention the
      cue? Admission detection via a rubric-guided LLM judge + researcher spot-checks
      of ≥50 judgments for judge accuracy.
- [ ] This yields a direct comparison to Chen et al.'s reveal rates.
- **Checkpoint:** judge-accuracy report.

### Phase 5 — Analysis & go/no-go (week 7)
- [ ] Compute pre-registered metrics (Section 6) per task type × model × condition.
- [ ] Statistics: proportions with 95% bootstrap CIs; logistic regression of
      answer-change on {task type, corruption type, position, model}; report effect
      sizes, not just p-values. Multiple-comparison correction across task types.
- [ ] Method agreement: do corruption and hint methods rank task types the same way?
- [ ] **Go/no-go:** researcher decides whether to proceed to Direction 2 or deepen
      Direction 1 (more models, prompt-phrasing controls, temperature sweeps).
- **Checkpoint:** analysis memo with figures.

### Phase 6 — Direction 2: activation probing (weeks 8–9, stretch)
- [ ] Verify TransformerLens support for the chosen model (see Section 4).
- [ ] Pipeline: binary/short-answer subset of tasks → generate CoT locally → extract
      residual-stream activations at each parsed step boundary → train linear probes
      (logistic regression on activations) to predict the final answer → plot probe
      confidence trajectory vs. step index.
- [ ] Probe hygiene: train/test split by **problem**, not by step; report probe
      accuracy on held-out problems; include a shuffled-label control.
- [ ] Comparison: at steps where CoT text expresses uncertainty/deliberation, what is
      the probe's confidence? Quantify "probe knows early" rate.
- [ ] Caveat to carry into writeup: early probe confidence is suggestive, not proof of
      non-causal CoT. Triangulate with the same model's Direction 1 results.
- **Checkpoint:** probing pilot report.

### Phase 7 — Writeup & release (weeks 10–12)
- [ ] Draft writeup in `writeup/`: motivation, methods, results (all conditions),
      limitations (prompt-distribution objection, single-model probing, judge noise),
      relation to Chen/Lanham/Tutek.
- [ ] Clean and publish repo + task dataset (check source dataset licenses first).
- [ ] Figures generated by scripts in `src/analysis/`, never hand-edited numbers.

---

## 6. Pre-registered metrics (fixed before running Phase 3)

1. **Corruption sensitivity** = P(final answer changes | corrupted CoT re-injected),
   per {task type, corruption type, position, model}. Higher = more faithful.
2. **Noticeability-adjusted sensitivity** = corruption sensitivity restricted to
   corruptions the second model detected (controls for invisible corruptions).
3. **Cue susceptibility** = P(answer shifts toward injected cue) − P(shift in
   matched no-cue control).
4. **Reveal rate** = P(CoT mentions the cue | answer shifted toward cue).
   Comparable to Chen et al.
5. **Method agreement** = rank correlation (Spearman) of task types by metric 1 vs.
   metric 4.
6. *(Direction 2)* **Early-knowledge rate** = fraction of problems where held-out probe
   confidence exceeds 0.9 before the CoT's midpoint step.

Exploratory analyses are allowed after these are computed and must be labeled as such.

---

## 7. Cost guardrails (recap)

- Estimated full Direction 1 pass: ~200 problems × ~10 calls × (2K in / 1.5K out)
  ≈ 10M in + 7.5M out tokens → roughly $25–150 per model depending on tier.
- Develop exclusively on the cheap workhorse. Frontier models run only finalized
  configs.
- Batch API + prompt caching wherever supported.
- Stop and alert the researcher at 50% and 80% of the $450 API budget.

---

## 8. Session workflow tips (for the researcher)

- Start each Claude Code session by asking it to read this file and the latest
  checkpoint memo, then state the current phase before doing anything.
- Use plan mode for any multi-file change; approve the plan before edits.
- Keep sessions scoped to one phase task; commit early and often with descriptive
  messages (`phase3: corruption taxonomy v1`).
- Ask Claude Code to write tests for `parsing.py` and `corruption.py` — these are the
  components whose silent failures would poison every downstream result.
- Periodically ask it to audit the cost log against provider dashboards.
