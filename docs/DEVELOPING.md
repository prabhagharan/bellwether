# Developing bellwether

How to set up, run, test, and extend bellwether on a host (without Docker). For the one-command
containerized run, see [../README-docker.md](../README-docker.md); for the architecture, see
[ARCHITECTURE.md](ARCHITECTURE.md).

> **Use `.venv/bin/python`, not `python`.** On the dev box the shell `python`/`pip`/`pytest`/`alembic`
> resolve to a system interpreter that shadows the project virtualenv. Always invoke the venv
> explicitly: `.venv/bin/python …`, `.venv/bin/alembic …`, `.venv/bin/python -m pytest …`.

## 1. Setup

```bash
# 1. Postgres (the whole app + the tests use a real Postgres — no mocking)
docker compose up -d db          # postgres:16 on localhost:5432, user/pw/db all `bellwether`

# 2. Python env — install the package + dev extras into the venv
.venv/bin/pip install -e ".[dev]"   # (if the venv isn't set up yet: python3.11 -m venv .venv first)

# 3. Config
cp .env.example .env             # then edit JWT_SECRET / ADMIN_PASSWORD
                                 # (.env is git-ignored and holds real secrets)

# 4. Migrations
.venv/bin/alembic upgrade head
```

`.env` (via `pydantic-settings`) supplies `DATABASE_URL`, `JWT_SECRET`, `ADMIN_USERNAME`/`ADMIN_PASSWORD`,
and the behaviour knobs (model names, thresholds, windows). Provider credentials are **plain env vars,
never Settings fields**: `ANTHROPIC_API_KEY` (or another LiteLLM-supported provider), `TAVILY_API_KEY`
(discovery gap-fill), `X_API_KEY` (the X connector + discovery X-verify). All are optional — the app
degrades gracefully when a key is absent. The admin user is seeded on API startup if the users table
is empty.

## 2. Run the pieces

Each is a separate process. Run the API + whichever workers you need.

```bash
# API  (create_app is a factory; Swagger at http://localhost:8000/docs)
.venv/bin/python -m uvicorn bellwether.api.app:create_app --factory --port 8000

# Workers  (one process per stage; each claims its own work via FOR UPDATE SKIP LOCKED)
.venv/bin/python -m bellwether.worker detect       # market-relevance classifier
.venv/bin/python -m bellwether.worker extract      # structured signal extraction
.venv/bin/python -m bellwether.worker resolve      # entity -> market symbol
.venv/bin/python -m bellwether.worker measure      # event-study impact
.venv/bin/python -m bellwether.worker discovery    # auto-discover a figure's sources
.venv/bin/python -m bellwether.worker alert        # evaluate alert rules -> webhooks + SSE
# add --once to drain the queue once and exit (default: run as a daemon)

# Prompt optimization (operator CLI, not an API)
.venv/bin/python -m bellwether.optimize run extract     # GEPA-optimize + champion/challenger
.venv/bin/python -m bellwether.optimize programs        # list versioned programs (* = champion)
.venv/bin/python -m bellwether.optimize promote <id>    # promote / roll back a champion
.venv/bin/python -m bellwether.optimize evals           # the eval-runs scoreboard

# Frontend  (Next.js dev server on :3000)
cd frontend && npm install && npm run dev
```

> **CORS for the frontend:** the dashboard runs on `http://localhost:3000` and calls the API from the
> browser, so add that origin to the backend before starting it:
> `CORS_ORIGINS=["http://localhost:3000"]` in `.env`.

## 3. Tests

```bash
.venv/bin/python -m pytest -q          # the whole suite, against the real Postgres
.venv/bin/python -m pytest tests/alerts -q   # a subset
```

- **Real Postgres**, not mocked — `docker compose up -d db` must be running.
- `tests/conftest.py` sets `LITELLM_LOCAL_MODEL_COST_MAP=True` so importing the DSPy/LiteLLM layer
  doesn't hang on a network cost-map fetch. Set the same env var for any ad-hoc script that imports
  the LLM layer.
- **Conventions:** use `owner_id=None` when seeding figures/sources in tests that don't exercise
  ownership (a non-null `owner_id` would violate the users FK); don't modify `tests/conftest.py` or
  `tests/api/conftest.py`.
- **Stubs hide integration bugs.** External adapters (LLM, market data, webhooks, Wikidata/Tavily) and
  the SSE/commit path are stubbed in the suite — verify them **live** before shipping a change that
  touches them. Real runs have caught bugs green tests missed (a fetch-window off-by-a-day, an SSL/CA
  gap, swallowed write errors).

## 4. Extending

The pipeline is built on frozen contracts + `build_*()` factories, so most extensions slot in without
touching the stages that consume them.

- **Add an ingestion connector** — implement the `SourceConnector` protocol (`fetch(self) -> list[RawItem]`,
  see `src/bellwether/connectors/base.py`) in a new module, then register it in
  `src/bellwether/connectors/registry.py::build_connector` under a new `connector_type`. (See `rss.py`
  and `x.py` for examples; the X connector ships disabled until `X_API_KEY` is set.)
- **Add a worker stage** — add a `make_<name>_stage(...) -> Stage` in `src/bellwether/worker.py`
  (a `claim_next` / `reclaim` / `process` triple), give it a claim column (a `status`/`due_at`-style
  field on the row it consumes), wire it into `_build_stage`, and add its name to the CLI `choices`
  in `worker.py`. Keep any heavy/network `build_*` import function-local inside `_build_stage`
  (the discovery + alert stages do this).
- **Swap an LLM paradigm** — the Detect/Extract/Resolve/Discovery modules sit behind frozen
  `Detector`/`Extractor`/`Resolver`/`Discoverer` contracts (`src/bellwether/llm/contracts.py`,
  `discovery/contracts.py`); change the module internals behind `build_*()` without touching the stages.
- **Optimize a module's prompt** — accumulate golden labels by reviewing pipeline output through the
  review API (`GET /review/queue`, `POST /review/{statement_id}`), then run
  `.venv/bin/python -m bellwether.optimize run extract` (or `detect`). GEPA compiles a challenger and
  promotes it only if it beats the current champion on the held-out split. The next worker start picks
  up the new champion via the champion-loading seam.

## 5. Docker

For the full containerized stack (Postgres + migrations + API + all six workers + the frontend in one
command), see **[../README-docker.md](../README-docker.md)**. `docker compose up db` (used above) starts
just Postgres for host development; `docker compose up --build` runs everything.
