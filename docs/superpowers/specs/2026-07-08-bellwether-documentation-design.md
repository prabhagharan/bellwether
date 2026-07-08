# bellwether — Project Documentation — Design Spec

**Date:** 2026-07-08
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Context:** The whole system is built (Plans 1–7b) + Dockerized, but the root `README.md` is an empty stub (`# bellwether`, no body). Rich internal docs exist (`docs/superpowers/specs/` + `plans/`) but there is **no reader-facing documentation** — a visitor to the repo sees a blank front door.

---

## 1. Goal

Reader-facing documentation that lets **anyone** understand the project — layered so a general reader gets the what/why/architecture and a developer or coding agent gets the concrete run/develop/extend detail. Diagrams as **Mermaid** (GitHub renders them). Every fact verified against the actual code, not recalled.

**In scope — six documents:**
- `README.md` (root, rewrite) — the layered front door.
- `AGENTS.md` (root, new) — the coding-agent contract (+ a `CLAUDE.md` one-line pointer).
- `docs/ARCHITECTURE.md` (new) — the technical deep-dive.
- `docs/DATA-MODEL.md` (new) — the tables + ER diagram + lifecycles.
- `docs/DEVELOPING.md` (new) — run / test / extend, links `README-docker.md`.

**Out of scope (deferred):** a hand-maintained `API.md` (OpenAPI at `/docs` + `/openapi.json` is authoritative), `CONTRIBUTING.md`, per-subsystem docs, dashboard screenshots/GIFs, a hosted docs site.

## 2. Principles

- **Layered, most-general-first.** README opens accessible (what/why/diagram/quickstart) and flows into concrete surface/layout; the deep material lives in the linked `docs/` files.
- **One job per file** — no file becomes a dumping ground. Human "how to run/extend" (`DEVELOPING.md`) is separate from the agent "rules you must not break" (`AGENTS.md`); the API is not re-documented by hand (points at OpenAPI).
- **Verified, not recalled.** Every table/endpoint/CLI-stage/env-var/path/command is checked against the repo as written (models, `/openapi.json`, `worker.py` choices, `config.py`, `.env.example`, compose). Runnable commands are the paths actually verified this project (the Docker quickstart was brought up + smoke-tested 5/5 live).
- **Drift-resistant.** Hand docs carry the stable conceptual layer; churny detail is deferred to self-updating sources (the live OpenAPI for the API; `docs/superpowers/specs/` for deep design rationale).
- **Mermaid diagrams**, syntax sanity-checked; fall back to a simpler shape rather than ship a broken block.

## 3. `README.md` (layered front door)

1. **Title + one-line pitch** — a single-user research system that ingests public statements of high-influence figures, extracts structured market signals with LLMs, and measures their real market impact.
2. **What & why** — the problem; the hard boundaries (read-only, provenance-guarded, no trading/generation/execution).
3. **System diagram** (Mermaid) — sources → ingest → pipeline → API / dashboard / alerts, with Postgres + the worker fleet.
4. **Feature tour** — the pipeline one line per stage (detect → extract → resolve → measure); source discovery; the evaluation/optimization flywheel + the Track-A/Track-B firewall; alerts (webhook + SSE); the dashboard.
5. **Quickstart** — Docker: `cp .env.docker.example .env` → `docker compose up --build` → open `:3000`; a pointer to `DEVELOPING.md` for host-dev.
6. **Surface at a glance** — worker CLI stages; the `python -m bellwether.optimize` CLI; "the REST API is self-documented at `/docs`" + a compact endpoint table (grouped: auth, watchlist, discovery, review, feed, alerts, stream).
7. **Project layout** — annotated short tree of `src/bellwether/` subsystems + `frontend/`.
8. **Status & how it was built** — Plans 1–7b complete + Dockerized; 2–3 lines on the spec-driven, TDD, live-verified discipline (for evaluators).
9. **Docs index** — links to ARCHITECTURE / DATA-MODEL / DEVELOPING / AGENTS.

## 4. `AGENTS.md` (agent contract) + `CLAUDE.md`

Terse, rule-shaped. `CLAUDE.md` is a one-line pointer (`See AGENTS.md`) so Claude Code auto-loads it.

1. **Orientation** — one line + links to README/ARCHITECTURE.
2. **How work is done here** — the spec → plan → subagent-TDD → per-task + whole-branch review → live-verify loop; specs/plans in `docs/superpowers/`; never execute a plan unapproved.
3. **Environment landmines** — `.venv/bin/python` (the shell `python` is a shadowing MacPorts 3.12); `docker compose up db` for Postgres; **`LITELLM_LOCAL_MODEL_COST_MAP=True`** for anything importing dspy (else litellm's cost-map fetch hangs); `.env` holds real secrets + is git-ignored → back up before overwriting.
4. **Test rules** — real Postgres (no mocking); `owner_id=None` in non-ownership tests; don't modify `tests/conftest.py`/`tests/api/conftest.py`; live-verify write endpoints + external adapters.
5. **Invariants you must not break** — the Track-A/Track-B firewall (`eval/` never imports `Impact`/`Resolution`); `worker.py` stays DSPy/market/webhook-free (function-local `build_*` imports); discovery's "LLM proposes, deterministic verification disposes"; provider-agnostic (model strings in `Settings`, credentials in env); the verbatim evidence-quote guard; idempotent queues (claim + unique); "stubs hide integration bugs → verify live."
6. **Where things live + how to run the suite.**

## 5. `docs/ARCHITECTURE.md` (technical deep-dive)

1. **Overview** — request/data flow + a Mermaid component graph (API, worker fleet, DB, the LLM/market/discovery/notifier adapters).
2. **The pipeline, stage by stage** — Ingest → Detect → Extract → Resolve → Measure; each: consumes, writes, model/logic, terminal-vs-retryable errors. Mermaid flow with `statements.status` transitions.
3. **Worker & queue model** — the generic `Stage` (claim/reclaim/process), `FOR UPDATE SKIP LOCKED`, claim-then-commit-before-slow-work, the CLI, and the *independent* claim columns (`statements.status`, `impacts.due_at`, `figures.discovery_status`, `extractions.alert_status`).
4. **The seams** — frozen contracts + `build_*()` factories (swappable-paradigm boundary), the champion-loading seam, provider-agnostic DSPy/LiteLLM, the market/discovery/notifier adapter registries.
5. **Evaluation & the firewall** — Track-A (golden labels → GEPA optimize → champion/challenger, versioned `dspy_programs`, held-out gate) vs Track-B (market-impact reporting/leaderboard); the enforced firewall.
6. **Source discovery** — Wikidata backbone + LLM/Tavily gap-fill + the deterministic confidence gate + review queue.
7. **Alerts** — the decoupled alert stage → per-rule webhooks + the DB-polled SSE feed.
8. **Cross-cutting invariants** — read-only/provenance, verbatim guard, owner-scoping, idempotency, verify-live.

## 6. `docs/DATA-MODEL.md`

1. **Mermaid ER diagram** of all ~15 tables + relationships.
2. **Per-table reference** grouped by shared-corpus (figures, sources, statements, detections, extractions, resolutions, entity_symbols, impacts, relevance_labels, extraction_labels, eval_runs, dspy_programs) vs per-user (users, alert_rules, alerts) — columns, the status/lifecycle fields that drive the queues, FKs.
3. **Lifecycle notes** — the four status flows (statement pipeline; impact due-queue; discovery; alert) and shared-corpus vs owner-scoped (nullable `owner_id`).

## 7. `docs/DEVELOPING.md`

1. **Host-dev setup** — venv (`.venv/bin/python`), Postgres (`docker compose up db`), `.env` from `.env.example`, migrations (`alembic upgrade head`).
2. **Run it** — API (uvicorn `--factory`), the six workers, the optimize CLI, the frontend (`npm run dev` + the CORS note).
3. **Tests** — `.venv/bin/python -m pytest`, real Postgres, the litellm cost-map env, the conventions (owner_id=None, verify-live).
4. **Extending** — add a connector, add a worker stage, optimize a module (concrete, pointing at the seams).
5. **Docker** — a short section linking `README-docker.md`.

## 8. Verification (accuracy pass — this is doc "testing")

- **Fact-check against the repo** while writing: tables/columns from `models/`; endpoints from `/openapi.json`; worker stages from `worker.py`; Settings/env from `config.py` + `.env.example`; compose services + file paths from the tree. No claim asserted from memory.
- **Mermaid syntax** parses for every diagram (and renders on GitHub); simplify rather than ship a broken block.
- **Commands run:** the Docker quickstart matches the live-verified path; host-dev commands match the established workflow.
- **Links resolve:** every README ↔ docs/ ↔ AGENTS relative link points at a real file.
- Final read-through with fresh eyes for placeholders/contradictions.

## 9. Deferred with intent

- Hand-maintained `API.md` (OpenAPI is authoritative).
- `CONTRIBUTING.md`, per-subsystem docs, dashboard screenshots/GIFs, a hosted docs site.
- Doc-tests / automated doc-drift checks (YAGNI for a solo project).
