# Local Dev Runner — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

Running the full stack for development currently means either `docker compose up --build`
(slow image rebuilds on every code change — no hot reload for the Python side) or manually
launching ~9 processes by hand (API + 7 workers + frontend), each needing the right
interpreter, the right env, and manual teardown. There is no one-command way to run all
components natively for fast iteration.

## Goal

A single command — `./scripts/dev.sh` — that starts every component natively on the host
(hot-reload where available), with Postgres in Docker, correct environment, and a clean
one-Ctrl-C shutdown.

## Decisions (locked)

1. **Postgres in Docker; everything else native.** The script starts the `db` container
   (`docker compose up -d db`) if needed; API, all 7 workers, and the frontend run on the
   host. ("No Docker for the app.")
2. **Process supervision via honcho + a Procfile.** honcho (a small Python process manager,
   added to the `dev` extras) reads a `Procfile`, runs all processes with colored
   per-process log prefixes, and propagates Ctrl-C to shut them all down cleanly.

## Architecture

Four files:

1. **`Procfile`** — declarative list of the 9 processes (see below).
2. **`scripts/dev.sh`** — entry point: preflight (Postgres, migrations, env, port check),
   then `exec honcho start`.
3. **`pyproject.toml`** — add `honcho` to the `dev` optional-dependencies list.
4. **`docs/DEVELOPING.md`** — document `./scripts/dev.sh` as the one-command dev path.

### Environment handling (the crux)

`dev.sh` must **NOT** `source .env` wholesale. The `.env` value
`CORS_ORIGINS=["http://localhost:3000"]` is JSON; a shell `source`/`export` strips the inner
double-quotes, yielding `[http://localhost:3000]`, which pydantic-settings then fails to
JSON-parse — the API crashes on startup with a `SettingsError`. (This was observed and
fixed by hand earlier in exactly this codebase.)

Instead, the proven-safe pattern (the same one used to run the API + workers by hand):

- Export **only** the three provider keys plus the litellm flag — all plain values with no
  brackets, so shell export is safe:
  - `export $(grep -E '^(ANTHROPIC_API_KEY|TAVILY_API_KEY|X_API_KEY)=' .env | xargs)`
  - `export LITELLM_LOCAL_MODEL_COST_MAP=True`
- Run honcho with **`-e /dev/null`** so honcho does **not** inject `.env` itself.

Each app process then receives:
- Provider keys (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `X_API_KEY`) from the inherited
  process environment (read via `os.environ` in the adapters).
- `LITELLM_LOCAL_MODEL_COST_MAP=True` from the inherited environment (prevents the
  litellm cost-map network fetch hanging on import).
- `DATABASE_URL`, `JWT_*`, `ADMIN_*`, `CORS_ORIGINS`, model names, thresholds — read by
  pydantic-settings directly from the `.env` **file** (`Settings.model_config` has
  `env_file=".env"`), correctly parsed, never shell-mangled.

The frontend reads `frontend/.env.local` for `NEXT_PUBLIC_API_BASE` (already present,
pointing at `http://localhost:8000`) — unchanged by this work.

**Assumption:** `.env` exists and its `DATABASE_URL` points at `localhost:5432` (the host-mode
value). The script does not create or rewrite `.env`.

### Preflight sequence in `dev.sh`

Before exec-ing honcho, in order:

1. **Postgres up:** `docker compose up -d db`, then poll readiness (e.g.
   `docker compose exec -T db pg_isready -U bellwether -d bellwether` or `pg_isready -h
   localhost -p 5432`) in a bounded loop until healthy or a timeout, erroring clearly on
   timeout.
2. **Migrations:** `.venv/bin/alembic upgrade head`.
3. **Port check:** if `:8000` or `:3000` is already in use (`lsof -ti:PORT`), exit with a
   clear message naming the port and how to free it — catches leftover/orphan processes.
4. **Exec honcho:** `exec .venv/bin/honcho start -e /dev/null "$@"` — `exec` so Ctrl-C
   reaches honcho, which SIGTERMs every child for a clean shutdown. `"$@"` passes through
   any process-name args (subset runs).

`dev.sh` uses `set -euo pipefail`. The provider-key exports and the litellm flag are set
after the preflight but before the honcho exec (so honcho's children inherit them).

### Procfile

```
api:       .venv/bin/python -m uvicorn bellwether.api.app:create_app --factory --reload --port 8000
detect:    .venv/bin/python -m bellwether.worker detect
extract:   .venv/bin/python -m bellwether.worker extract
resolve:   .venv/bin/python -m bellwether.worker resolve
measure:   .venv/bin/python -m bellwether.worker measure
discovery: .venv/bin/python -m bellwether.worker discovery
alert:     .venv/bin/python -m bellwether.worker alert
ingest:    .venv/bin/python -m bellwether.worker ingest
web:       npm --prefix frontend run dev
```

## Usage

- `./scripts/dev.sh` — runs **all** processes; one Ctrl-C stops everything.
- `./scripts/dev.sh api web ingest` — honcho runs only the **named subset** (args pass
  through), for when the full 7-worker fleet isn't wanted.

## Error handling

- `set -euo pipefail` — any preflight step failing aborts before honcho starts.
- Postgres readiness has a bounded wait + clear timeout error.
- Port-in-use is caught with an actionable message rather than a confusing
  bind error deep in uvicorn/next.
- Process crashes at runtime: honcho reports the exit in its log stream; the process is
  not auto-restarted (a crash should be seen and fixed — see YAGNI).

## Testing / verification

A launcher script is not unit-testable; verification is a live smoke run (final plan step):

1. `./scripts/dev.sh` starts cleanly (preflight passes, honcho launches all).
2. `GET http://localhost:8000/openapi.json` → 200; `GET http://localhost:3000` → 200.
3. At least one worker logs activity (e.g. an `ingest`/`discovery` poll line).
4. Ctrl-C → honcho shuts all down; afterward `lsof -ti:8000` and `lsof -ti:3000` are empty
   (no orphaned processes).
5. `./scripts/dev.sh api` runs only the API (subset pass-through works).

Also verified: `.venv/bin/pip install -e ".[dev]"` installs honcho, and `.venv/bin/honcho`
is present.

## Out of scope (YAGNI)

- No auto-restart-on-crash beyond honcho's default (a crashed process stays down).
- No host-native Postgres path (the DB runs in Docker by decision 1).
- No `.env` bootstrap/scaffolding (assumes a valid host-mode `.env`).
- No changes to `docker-compose.yml`, the app code, or the frontend.
