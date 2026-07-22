# Environment setup & reproducibility (Milestone A1)

This project uses a project-local virtual environment pinned to an exact
interpreter and a fully-resolved transitive dependency lock. The goal is that a
fresh checkout reproduces the tested environment byte-for-byte.

## Pinned interpreter

| Field | Value |
|---|---|
| Python | 3.13.5 (CPython) |
| OS (lock captured on) | macOS / Darwin 15.7.3 |
| Architecture | arm64 |
| Lock captured | 2026-07-21 |

`.python-version` records `3.13.5` for tooling (pyenv, VS Code interpreter
selection). Use the same minor version (3.13.x) to stay within the locked set.

## Files

- `requirements.txt` — human-edited **direct** dependency pins (ground rule 4).
- `requirements.lock.txt` — machine-generated **full transitive** lock
  (`pip freeze`) with a platform-provenance header. Never hand-edit; regenerate
  it when `requirements.txt` changes.

## Recreate the environment from scratch

```bash
cd /Users/daniel/Documents/coding/nontrivial
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock.txt
```

This was verified on 2026-07-21: a fresh venv installed from the lock produced a
`pip freeze` identical to `requirements.lock.txt` and passed the full offline
test suite (72 passed).

## Running tests

The default suite is **offline** and must never make network calls or require API
keys (ground rule: unit tests never require network access).

```bash
# Offline suite (default). Strip API keys to guarantee no accidental live calls:
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY \
    -u OPENROUTER_API_KEY -u TOGETHER_API_KEY -u DEEPSEEK_API_KEY \
    .venv/bin/python -m pytest -q
```

Live-API tests (real paid provider calls: capability probes, smoke test) are
marked `@pytest.mark.live_api` and **excluded by default** via `pytest.ini`
`addopts = -m "not live_api"`. They are opt-in only:

```bash
# Run ONLY live-API tests (requires keys in .env; incurs cost — see cost guardrails):
.venv/bin/python -m pytest -m live_api
```

`--strict-markers` is enabled, so an unregistered/typo'd marker is an error rather
than silently running in the default suite. New markers must be registered in
`pytest.ini`.

## Regenerating the lock

After changing a pin in `requirements.txt`:

```bash
.venv/bin/python -m pip install -r requirements.txt   # apply the change
.venv/bin/python -m pip freeze > requirements.lock.txt
# then restore the metadata header block at the top of requirements.lock.txt
```

## VS Code / Pylance

Point the interpreter at `.venv/bin/python` so Pylance and the terminal test
runner use the same environment. Project-owned modules (`src/`, `scripts/`,
`tests/`) must have zero unresolved-import diagnostics against this interpreter.

## Not yet installed (later phases)

`pandas`, `numpy`, `torch`, `transformers`, `transformer-lens`, `scikit-learn`,
`matplotlib` are deliberately absent until Phases 5/6 (analysis and probing).
