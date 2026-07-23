# CLAUDE.md — pointer

All project documentation now lives in **`README.md`** (consolidated 2026-07-23).

This file is kept as a thin pointer because Claude Code auto-loads `CLAUDE.md`
as project-level instructions at session start.

Start here:

- **Part I of `README.md`** — research design, ground rules, model configuration,
  phased task list, pre-registered metrics, cost guardrails, decisions log
  (the former contents of this file).
- **Part II of `README.md`** — implementation plan, milestone status, handoff
  state (the former `plan.md`).
- Top of `README.md` — repository structure, setup/validation commands, current
  status.

Ground rules still in force (summary; full text in README Part I §2):

1. Never fabricate experimental results.
2. Cost discipline: estimates only from `src/costs.py`; confirm anything >$5;
   hard budget $450.
3. Human-in-the-loop for science: propose, don't silently change, design.
4. Reproducibility: config + thinking setting + seed per run; pinned deps.
5. No cherry-picking; pre-registered metrics first.
6. Small before big (5 problems before 50, one model before the panel).
7. Secrets in `.env`; never print or commit keys.
8. Response caching keyed on the full semantic request.

Current status snapshot: Milestones A–C4 complete. Pending researcher checkpoint:
review C4 replication report + 14 shifter transcripts before Phase 1 sign-off.
Known open issue: math surface perturbations are sparse (41/50 math tasks have
empty transformation logs) — policy decision needed before expanding.
