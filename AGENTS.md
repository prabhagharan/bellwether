# AGENTS.md

bellwether — LLM-driven market-signal research pipeline; see [README](README.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How work is done here

spec → plan → subagent-driven TDD → per-task review → whole-branch review → **live/e2e verification** before merge. Specs and plans live in `docs/superpowers/specs/` and `docs/superpowers/plans/`. Never execute a plan that hasn't been explicitly approved, and never execute one that isn't merged into the branch you're working from.

## Environment landmines

- `.venv/bin/python -m pytest …` / `.venv/bin/alembic …` — the shell's bare `python`/`pytest` are a shadowing MacPorts 3.12, not this project's venv. Bare invocations silently use the wrong interpreter.
- Postgres: `docker compose up db` (or the full stack) before running anything. Tests hit a **real** Postgres — there is no sqlite/in-memory fallback.
- **`LITELLM_LOCAL_MODEL_COST_MAP=True`** for anything importing dspy (litellm fetches its cost-map over the network on import and can hang for minutes under load). It's set in `tests/conftest.py`; set it yourself for ad-hoc scripts or gen steps that import the LLM layer outside pytest.
- `.env` is git-ignored and holds **real secrets**. If a task must overwrite it (e.g. swap in `.env.docker.example`), back it up first and restore it afterward. Never commit a real `.env`.

## Test rules

- Real Postgres, no mocking of the database — `db_session` runs each test inside a rolled-back transaction/savepoint.
- Use `owner_id=None` in tests that aren't specifically testing ownership, or you'll hit FK violations.
- Do **not** modify `tests/conftest.py` or `tests/api/conftest.py`.
- API write endpoints and external adapters (LLM, market, webhook, Wikidata/web-search) are stubbed in the suite. Stubs hide integration bugs — **verify write paths and adapters live before merge**.

## Invariants you must not break

- **Firewall:** `bellwether.eval.*` must never import `Impact`/`Resolution` — a Track-A (extraction accuracy) score must be invariant to market data. Enforced by `tests/test_firewall.py`.
- **`worker.py` lazy-imports the optional integrations:** the discovery adapters (Wikidata, web-search, X-verify, HTTP) and the alert webhook notifier are imported function-locally inside `_build_stage`, not at module top — so those third-party/network-touching integrations don't load unless that stage actually runs. (Core detect/extract/resolve/measure `build_*` are ordinary top-level imports; only the discovery/webhook side is lazy.)
- **Discovery:** "the LLM proposes, deterministic verification disposes" — an LLM/Tavily-proposed source binding can never auto-enable itself; only cross-referenced-authority signals clearing the confidence gate (`discovery_confidence_threshold`) flip it to enabled.
- **Provider-agnostic:** model names are plain `Settings` string fields (e.g. `detect_model`, `extract_model`); credentials (`X_API_KEY`, `TAVILY_API_KEY`, provider API keys) are read directly from the environment — never added as `Settings` fields.
- **Anti-fabrication:** an extraction's (and a corrected gold label's) `evidence_quote` must be a verbatim substring of the source statement text, checked via `is_verbatim`.
- **Idempotent queues:** claim-then-commit-before-slow-work (the row is claimed and the transaction committed before any slow LLM/market/network call runs); unique constraints guard against duplicate re-fires.

## Where things live / run the suite

`src/bellwether/` holds `api/`, `llm/` (dspy programs), `market/`, `discovery/`, `alerts/`, `measure/`, `eval/`, `models/`, `repositories/`, plus `worker.py` (the stage runner) and `config.py` (`Settings`). Run the full suite against a real Postgres with:

```
.venv/bin/python -m pytest -q
```

The worker (`bellwether.worker`) and optimizer (`bellwether.eval.optimize`) are invoked as CLIs. For run/extend mechanics, see [docs/DEVELOPING.md](docs/DEVELOPING.md).
