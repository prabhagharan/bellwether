# Local Dev Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single command `./scripts/dev.sh` that runs the whole stack natively (API + 7 workers + frontend) with Postgres in Docker, correct env, and clean one-Ctrl-C shutdown.

**Architecture:** A `Procfile` lists the 9 processes; `honcho` (added to the `dev` extras) supervises them with prefixed logs and signal propagation. `scripts/dev.sh` does the preflight (start `db` container, wait ready, `alembic upgrade`, port check, export the env-safe subset) then `exec`s honcho.

**Tech Stack:** honcho (Python process manager), bash, Docker (Postgres only), the existing venv.

## Global Constraints

- **Postgres in Docker; everything else native.** `docker compose up -d db`; API/workers/frontend run on the host.
- **Supervision = honcho + Procfile.** Add `honcho` to `pyproject.toml` `dev` extras.
- **NEVER `source .env` wholesale.** `CORS_ORIGINS=["http://localhost:3000"]` is JSON; a shell source strips the inner quotes → invalid JSON → the API crashes with a pydantic `SettingsError`. Instead: export ONLY the three provider keys plus the litellm flag (plain values, shell-safe), and run honcho with `-e /dev/null` so honcho does not inject `.env`. pydantic-settings reads `.env` (its `env_file=".env"`) directly for `DATABASE_URL`/`JWT`/`CORS_ORIGINS`/etc.
  - `export $(grep -E '^(ANTHROPIC_API_KEY|TAVILY_API_KEY|X_API_KEY)=' .env | xargs)`
  - `export LITELLM_LOCAL_MODEL_COST_MAP=True`
- **`set -euo pipefail`** in `dev.sh`; bounded Postgres-readiness wait with a clear timeout error; **port-in-use check for `:8000` and `:3000`** before launching; `exec .venv/bin/honcho … start "$@"` (pass args through for subset runs).
- **Assume `.env` exists** with `DATABASE_URL` at `localhost:5432`. Do not create/rewrite `.env`.
- **No changes** to `docker-compose.yml`, app code (`src/`), or the frontend. New files only + `pyproject.toml` + `docs/DEVELOPING.md`.
- The 9 processes: `api`, `detect`, `extract`, `resolve`, `measure`, `discovery`, `alert`, `ingest`, `web`.
- DB container creds: user/password/db all `bellwether`; service name `db`.

---

### Task 1: honcho dependency + Procfile

**Files:**
- Modify: `pyproject.toml` (line 22 — add `honcho` to the `dev` list)
- Create: `Procfile` (repo root)

**Interfaces:**
- Produces: a `Procfile` at repo root defining the 9 named processes; `.venv/bin/honcho` installed. `scripts/dev.sh` (Task 2) execs `honcho … start` against this Procfile.

- [ ] **Step 1: Add honcho to the dev extras**

In `pyproject.toml`, change line 22 from:

```
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.4"]
```

to:

```
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.4", "honcho>=1.1"]
```

- [ ] **Step 2: Install it into the venv**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: installs `honcho` (and confirms the others already satisfied); no errors.

- [ ] **Step 3: Verify honcho is available**

Run: `.venv/bin/honcho --version`
Expected: prints a version (e.g. `1.1.0`), exit 0.

- [ ] **Step 4: Create the `Procfile`**

Create `Procfile` at the repo root with exactly:

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

- [ ] **Step 5: Verify honcho parses the Procfile and sees all 9 processes**

Run: `.venv/bin/honcho -f Procfile check`
Expected: prints `Valid procfile detected (api, detect, extract, resolve, measure, discovery, alert, ingest, web)` (order/wording may vary slightly, but **all 9 names must be listed**), exit 0.

(If `honcho -f Procfile check` errors on flag placement, run `.venv/bin/honcho --help` to confirm where `-f` goes — global options precede the subcommand — and use that form.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml Procfile
git commit -m "feat: add honcho + Procfile for the native dev stack"
```

---

### Task 2: `scripts/dev.sh` entry point + docs

**Files:**
- Create: `scripts/dev.sh` (executable)
- Modify: `docs/DEVELOPING.md` (add a one-command dev section near §2 "Run the pieces", line 35)

**Interfaces:**
- Consumes: the `Procfile` and installed `honcho` from Task 1.
- Produces: `./scripts/dev.sh [process...]` — one-command native stack.

- [ ] **Step 1: Create `scripts/dev.sh`**

Create `scripts/dev.sh` with exactly this content:

```bash
#!/usr/bin/env bash
# One-command native dev stack: Postgres in Docker, everything else on the host.
# Usage:
#   ./scripts/dev.sh                 # run all processes (api + 7 workers + web)
#   ./scripts/dev.sh api web ingest  # run only the named subset
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- sanity ---
[ -x .venv/bin/python ] || { echo "error: .venv not found — run: python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'"; exit 1; }
[ -f .env ]             || { echo "error: .env not found — copy .env.example to .env and set DATABASE_URL to localhost"; exit 1; }
[ -x .venv/bin/honcho ] || { echo "error: honcho not installed — run: .venv/bin/pip install -e '.[dev]'"; exit 1; }

# --- 1. Postgres (Docker) up + healthy ---
echo "==> starting Postgres (docker compose db)…"
docker compose up -d db
for i in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U bellwether -d bellwether >/dev/null 2>&1; then
    break
  fi
  [ "$i" = 30 ] && { echo "error: Postgres did not become ready in time"; exit 1; }
  sleep 1
done
echo "==> Postgres ready"

# --- 2. migrations ---
echo "==> applying migrations…"
.venv/bin/alembic upgrade head

# --- 3. port check (catch leftover/orphan processes) ---
for port in 8000 3000; do
  if lsof -ti:"$port" >/dev/null 2>&1; then
    echo "error: port $port is already in use (pid $(lsof -ti:"$port" | tr '\n' ' '))."
    echo "       free it first, e.g.:  lsof -ti:$port | xargs kill"
    exit 1
  fi
done

# --- 4. env: export ONLY the provider keys + litellm flag (never 'source .env' — it mangles
#         CORS_ORIGINS's JSON). pydantic-settings reads .env for DATABASE_URL/JWT/CORS itself. ---
export $(grep -E '^(ANTHROPIC_API_KEY|TAVILY_API_KEY|X_API_KEY)=' .env | xargs) 2>/dev/null || true
export LITELLM_LOCAL_MODEL_COST_MAP=True

# --- 5. launch everything; honcho does NOT load .env (-e /dev/null). "$@" = optional subset. ---
echo "==> launching stack (Ctrl-C to stop all)…"
exec .venv/bin/honcho -f "$ROOT/Procfile" -e /dev/null start "$@"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/dev.sh`
Expected: no output; `test -x scripts/dev.sh && echo ok` prints `ok`.

- [ ] **Step 3: Verify the preflight + env + honcho launch, API-only subset (bounded live run)**

This proves the whole mechanism without the heavy full fleet. Run a bounded background launch of just the `api` process, probe it, then stop it:

```bash
timeout 30 ./scripts/dev.sh api > /tmp/devsh.log 2>&1 &
DEV_PID=$!
for i in $(seq 1 20); do curl -sf -o /dev/null http://localhost:8000/openapi.json && break; sleep 1; done
curl -s -o /dev/null -w "api /openapi.json -> %{http_code}\n" http://localhost:8000/openapi.json
kill "$DEV_PID" 2>/dev/null || true
wait "$DEV_PID" 2>/dev/null || true
sleep 2
echo "orphans on :8000 -> $(lsof -ti:8000 | tr '\n' ' ' || echo none)"
```

Expected: `api /openapi.json -> 200` (the API started, which also proves the env handling — a CORS-mangled env would have crashed startup); after kill, `orphans on :8000 -> ` (empty — clean shutdown). Inspect `/tmp/devsh.log` to confirm the preflight lines ("Postgres ready", "applying migrations", "launching stack") and no `SettingsError`.

- [ ] **Step 4: Verify the port-in-use guard**

With nothing running on 8000, occupy it and confirm the script refuses to start:

```bash
.venv/bin/python -m http.server 8000 >/dev/null 2>&1 &
BLOCK=$!
sleep 1
./scripts/dev.sh api; echo "exit=$?"
kill "$BLOCK" 2>/dev/null || true
```

Expected: prints `error: port 8000 is already in use …` and `exit=1` (the guard fired before honcho launched).

- [ ] **Step 5: Document it in `docs/DEVELOPING.md`**

In `docs/DEVELOPING.md`, immediately after the `## 2. Run the pieces` heading (line 35), insert a one-command subsection before the existing per-process instructions:

```markdown
### One command (recommended)

Run the whole stack natively with Postgres in Docker:

```bash
./scripts/dev.sh              # API + all 7 workers + frontend, native, hot-reload
./scripts/dev.sh api web ingest   # only the named processes
```

`scripts/dev.sh` starts the `db` container, applies migrations, checks ports `8000`/`3000`
are free, exports the provider keys, and runs everything under `honcho` (colored per-process
logs; one Ctrl-C stops all). The individual commands below are what it runs, for when you
want to launch a piece by hand.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/dev.sh docs/DEVELOPING.md
git commit -m "feat: scripts/dev.sh — one-command native dev stack (honcho preflight)"
```

---

## Final live verification (before merge, not a task)

Full-stack smoke (the real thing):

```bash
./scripts/dev.sh
# in another shell, once it's up:
curl -s -o /dev/null -w "api  -> %{http_code}\n" http://localhost:8000/openapi.json   # 200
curl -s -o /dev/null -w "web  -> %{http_code}\n" http://localhost:3000                 # 200
# confirm a worker logged a poll (ingest/discovery) in honcho's output
# Ctrl-C, then:
lsof -ti:8000 :3000    # expect empty — no orphans
```

Expected: both surfaces 200, at least one worker logs activity, and Ctrl-C leaves no
orphaned processes. Also confirm `./scripts/dev.sh api` runs only the API (subset works).

## Self-review notes

- **Spec coverage:** honcho + Procfile (Task 1) ✓; dev.sh preflight — db up + wait + migrate + port check (Task 2 Step 1) ✓; env-safe export + `-e /dev/null` (Task 2 Step 1 + Global Constraints) ✓; subset pass-through `"$@"` ✓; docs (Task 2 Step 5) ✓; live verification (final section + Task 2 Steps 3–4) ✓. No app/compose/frontend changes ✓.
- **Placeholder scan:** none — every step has concrete content/commands.
- **Consistency:** the Procfile process names (api/detect/extract/resolve/measure/discovery/alert/ingest/web) match the worker CLI `choices` and the honcho `check` expectation; `-e /dev/null` and the export subset are identical between Global Constraints and the dev.sh body.
- All `curl -w` uses a single `%{http_code}` (plain shell commands, not printf/heredoc).
