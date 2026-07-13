Next steps (with API access)

1. Get a DeepSeek API key — it's the only key Phase 0 needs, since the workhorse is deepseek-v4-flash. Then cp .env.template .env and paste it in. (DeepSeek requires a small prepaid top-up, ~$5–10; that's plenty — the smoke test itself is fractions of a cent.)
2. Run the smoke test: python scripts/smoke_test.py. It runs 5 hardcoded problems end-to-end and doubles as the live capability probe the matrix depends on: does beta Chat Prefix Completion actually continue from a prefix, does non-thinking mode emit zero reasoning_content, and what's the correct non-thinking request parameter after the 2026-07-24 legacy-name sunset. Those answers gate the whole Phase 3 design.
3. Review the Phase 0 checkpoint yourself: the JSONL output schema in data/results/, the cost log, and the capability matrix's open-questions section (docs/capability_matrix.md, section 4).
4. Two decisions only you can make:
  - Whether to trigger the literature refresh now (it's token-heavy; the plan is a slimmer fresh sweep). It only truly blocks the writeup's positioning, so you could defer it to Phase 2–3 timeframe.
  - The closed-frontier panel model (Sonnet 5 / Opus 4.8 vs. GPT-5.4 vs. legacy Sonnet 4.6) — still open in Section 4. This doesn't block Phase 1, so it can also wait.
5. Then start Phase 1 (the task set — see below), which is where most of the real work is anyway.

Plan without API access

A surprising amount of Phase 1–3 is API-free, and there's also a zero-cost way to exercise the live pipeline. In rough priority order:

1. Build the task set (Phase 1, the biggest API-free chunk). Downloading GSM8K from Hugging Face is free (needs internet, not API keys). Write the surface-perturbation script (change numbers/names, preserve structure, record seed + original ID), and curate the logic puzzles, multi-step factual questions, and ethical dilemmas. Getting to 4 × 50 problems with binary_ok flags (≥20 per type), ground truth, and license fields is a solid week-plus of work that requires zero API calls.
2. Write the per-task-type corruption spec (a Phase 3 prerequisite that must be researcher-approved before coding). This is a pure design document: what does "subtle error" mean for an ethical dilemma? Which conditions are N/A per task type? Doing this now means Phase 3 coding starts immediately once you have access.
3. Build and test the deterministic code paths. parsing.py's numbered-step and sentence-segmentation strategies, corruption.py's taxonomy mechanics, and evaluation.py's exact/normalized answer matching can all be written and unit-tested against handcrafted fixtures, with the LLM-judge fallbacks stubbed behind an interface. CLAUDE.md specifically asks for tests on exactly these three modules.
4. Create the researcher-labeled validation sets you'll need later: the ≥50 hand-labeled answer pairs for the equivalence judge can be constructed by hand now. (The 32 hand-labeled CoTs for the parser do need generated CoTs, so that part waits — or see point 5.)
5. Run the bridge model locally. DeepSeek-R1-Distill-Qwen-7B is a panel model that requires no API at all. Verifying it loads in TransformerLens is literally the first Direction 2 task, and generating CoTs from it locally (HF transformers; on your Mac via MPS, or free-tier Colab) gives you real CoT text to develop the parser against and even dry-run the corruption pipeline — a full end-to-end rehearsal at $0.
6. Paper decisions: closed-frontier choice and cost planning from the matrix — reading and deciding, no spend.

The only things genuinely blocked without API access are the smoke test's live capability probes (prefix completion, zero-thinking confirmation), the panel runs themselves, and the LLM-judge components — and by the time you have keys, everything else would be ready so the smoke test is the only thing standing between you and Phase 3.