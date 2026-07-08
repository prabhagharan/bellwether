# bellwether — Dockerization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docker compose up` runs the whole bellwether system — Postgres + migrations + API + 6 workers + frontend — from two multi-stage images, config from one `.env`.

**Architecture:** One Python image (reused by the migrator + API + 6 workers, differing only by compose `command`) and one Node image (built Next.js SPA). Startup graph `db → migrator → {api, workers} → frontend`, gated on migrations via `service_completed_successfully`. Layer-ordered Dockerfiles so code changes rebuild in seconds; deps reinstall only on `pyproject.toml`/`package.json` change. Migrations are incremental (Alembic tracks the applied revision; alembic reads `DATABASE_URL` from Settings — no ini editing).

**Tech Stack:** Docker + Compose v2, `python:3.11-slim`, `node:22-slim`, Postgres 16. Design spec: `docs/superpowers/specs/2026-07-08-bellwether-dockerize-design.md`.

## Global Constraints

- **Two images only:** the Python image builds once and is reused by `migrator` + `api` + `detect`/`extract`/`resolve`/`measure`/`discovery`/`alert`; the Node image is the built frontend. Every Python service overrides `command` in compose.
- **Migrator is the schema gate:** `api` + all 6 workers `depends_on: { migrator: { condition: service_completed_successfully } }`; `migrator depends_on: { db: { condition: service_healthy } }`.
- **Alembic reads `DATABASE_URL` from Settings** (`migrations/env.py:8` → `get_settings().database_url`) — the migrator only needs `DATABASE_URL` in its env; do NOT edit `alembic.ini`.
- **`DATABASE_URL` inside compose uses host `db`:** `postgresql+psycopg://bellwether:bellwether@db:5432/bellwether` (not `localhost`).
- **Frontend API URL is browser-facing + build-time:** `NEXT_PUBLIC_API_BASE` is a **build arg** defaulting to `http://localhost:8000` (NOT `http://api:8000` — the browser can't resolve service names). It's inlined into the client bundle at build; a runtime env var would not take effect.
- **`CORS_ORIGINS=["http://localhost:3000"]`** in the API's env so the browser-loaded frontend can call it.
- **Bake `LITELLM_LOCAL_MODEL_COST_MAP=True`** into the Python image env (dspy-importing workers hang on litellm's cost-map fetch otherwise).
- **Non-root** runtime users in both images; slim runtime stages (no build toolchain / tests / .git).
- **`docker compose up db` must still start just Postgres** for host-dev pytest (published `localhost:5432` unchanged).
- **Docker is NOT runnable in this sandbox.** `docker …` commands run UNSANDBOXED (dangerouslyDisableSandbox) and may still fail if no daemon — a task that can't reach a Docker daemon reports its state honestly (validate what's possible: `docker compose config` needs only the CLI; `build`/`up` need a daemon). Do NOT fake a successful build/up.
- **Secrets:** `.env` is git-ignored; ship `.env.docker.example` (placeholders only). Never commit a real `.env`.

## File Structure

```
/
├── Dockerfile               # (new) Python: migrator + api + workers
├── .dockerignore            # (new)
├── docker-compose.yml       # (modify) db-only -> full stack
├── .env.docker.example      # (new)
├── README-docker.md         # (new) short run instructions
└── frontend/
    ├── Dockerfile           # (new) Node: built SPA (standalone)
    ├── .dockerignore         # (new)
    └── next.config.mjs      # (modify) output: "standalone"
```

---

### Task 1: Python image (Dockerfile + .dockerignore)

**Files:** Create `Dockerfile`, `.dockerignore`.

**Interfaces:** Produces a Python image that (a) has the `bellwether` package + deps installed in a venv on PATH, (b) includes `alembic.ini` + `migrations/` + `src/`, (c) runs as non-root, (d) sets `LITELLM_LOCAL_MODEL_COST_MAP=True`, (e) has no meaningful default CMD (compose sets `command`).

- [ ] **Step 1: Write `.dockerignore` (root)**

```
.venv
__pycache__
**/__pycache__
*.pyc
.pytest_cache
.git
.gitignore
tests
docs
.superpowers
frontend
.env
*.md
.ruff_cache
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN python -m venv /opt/venv
# Dep layer first (cached unless pyproject changes), then the source.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 LITELLM_LOCAL_MODEL_COST_MAP=True
# psycopg[binary] ships its own libpq; no extra apt needed for the slim runtime.
COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
# Non-root
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser
# No default CMD — compose sets `command` per service (uvicorn / worker / alembic).
```
NOTE on the dep-caching nuance: `pip install .` needs `src/` present (it builds the package), so `COPY src` precedes it — meaning a **source** change *does* invalidate the dep layer in this single-install form. To get true "code change doesn't reinstall deps," the standard trick is to install deps without the project first. If build speed on code changes matters, use this variant instead (still correct, better cached):
```dockerfile
# builder — deps cached independent of src:
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "sqlalchemy>=2.0" "alembic>=1.13" \
    "psycopg[binary]>=3.1" "pydantic-settings>=2.2" "pyjwt>=2.8" "passlib[bcrypt]>=1.7" \
    "bcrypt>=4.0,<4.1" "python-multipart>=0.0.9" "feedparser>=6.0" "dspy>=2.5" "yfinance>=0.2"
COPY src ./src
RUN pip install --no-deps .
```
Use the **second (deps-explicit) variant** — it matches the spec's "code changes don't reinstall deps." Keep the dep list in sync with `pyproject.toml` (a comment in the Dockerfile should say so).

- [ ] **Step 3: Validate the build context + Dockerfile parse (UNSANDBOXED)**

Run: `docker build -t bellwether-py:test . ` (unsandboxed).
Expected: builds successfully; the dep layer caches on a second `docker build` after touching `src/` (only the `COPY src` + `pip install --no-deps .` layers rerun). If no Docker daemon is available, run at least `docker build --check .` (Dockerfile lint/parse, no daemon needed on newer buildx) and report that the full build needs the user's daemon.
Sanity (if built): `docker run --rm bellwether-py:test python -c "import bellwether; print('ok')"` and `docker run --rm bellwether-py:test alembic --version`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(docker): Python image (migrator + api + workers), layer-cached deps"
```

---

### Task 2: Frontend image (standalone) + .dockerignore

**Files:** Create `frontend/Dockerfile`, `frontend/.dockerignore`; modify `frontend/next.config.mjs`.

**Interfaces:** Produces a Node image serving the production Next build on `:3000`, with `NEXT_PUBLIC_API_BASE` baked at build time from a build arg (default `http://localhost:8000`).

- [ ] **Step 1: Enable standalone output in `frontend/next.config.mjs`**

```js
export default { output: "standalone" };
```
(Next `standalone` emits a minimal self-contained server in `.next/standalone` — the runtime image copies just that + static assets, no full `node_modules`.)

- [ ] **Step 2: Write `frontend/.dockerignore`**

```
node_modules
.next
.git
openapi.json
npm-debug.log*
.env*.local
```

- [ ] **Step 3: Write `frontend/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

FROM node:22-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000
# Standalone output = self-contained server + minimal node_modules.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
RUN useradd --create-home --uid 10002 nextuser 2>/dev/null || true
USER nextuser
EXPOSE 3000
CMD ["node", "server.js"]
```
NOTE: `public/` may not exist yet — if `frontend/public/` is absent, either create an empty `frontend/public/.gitkeep` or drop that COPY line. Confirm during the build; adjust to whichever compiles.

- [ ] **Step 4: Build (UNSANDBOXED)**

Run: `docker build -t bellwether-web:test ./frontend` (unsandboxed).
Expected: `npm ci` (uses the committed lockfile) + `npm run build` succeed; the standalone `server.js` is produced. If no daemon, run `docker build --check ./frontend` and report the full build needs the user's daemon.
Sanity (if built): `docker run --rm -p 3000:3000 bellwether-web:test` then `curl -s -o /dev/null -w "%{http_code}" localhost:3000` → 200 (then stop it).

- [ ] **Step 5: Commit**

```bash
git add frontend/Dockerfile frontend/.dockerignore frontend/next.config.mjs
git commit -m "feat(docker): frontend image (Next standalone build, browser-facing API base build-arg)"
```

---

### Task 3: docker-compose.yml (full stack) + env template + README

**Files:** Modify `docker-compose.yml`; create `.env.docker.example`, `README-docker.md`.

**Interfaces:** Produces the full compose stack (db + migrator + api + 6 workers + frontend) with the correct dependency graph, plus the config template.

- [ ] **Step 1: Write `.env.docker.example`**

```bash
# Copy to .env before `docker compose up`.  DATABASE_URL uses the compose service host `db`.
DATABASE_URL=postgresql+psycopg://bellwether:bellwether@db:5432/bellwether
JWT_SECRET=change-me-to-a-secure-random-secret-at-least-32-bytes
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
CORS_ORIGINS=["http://localhost:3000"]
# Optional provider keys (workers keyless-degrade if unset):
# ANTHROPIC_API_KEY=
# TAVILY_API_KEY=
# X_API_KEY=
```

- [ ] **Step 2: Rewrite `docker-compose.yml` (full stack)**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: bellwether
      POSTGRES_PASSWORD: bellwether
      POSTGRES_DB: bellwether
    ports:
      - "5432:5432"
    volumes:
      - bw_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bellwether -d bellwether"]
      interval: 5s
      timeout: 3s
      retries: 10

  migrator:
    build: .
    command: alembic upgrade head
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

  api:
    build: .
    command: uvicorn bellwether.api.app:create_app --factory --host 0.0.0.0 --port 8000
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      migrator:
        condition: service_completed_successfully
    restart: unless-stopped

  detect:   { build: ., command: python -m bellwether.worker detect,    env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }
  extract:  { build: ., command: python -m bellwether.worker extract,   env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }
  resolve:  { build: ., command: python -m bellwether.worker resolve,   env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }
  measure:  { build: ., command: python -m bellwether.worker measure,   env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }
  discovery:{ build: ., command: python -m bellwether.worker discovery, env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }
  alert:    { build: ., command: python -m bellwether.worker alert,     env_file: .env, depends_on: { migrator: { condition: service_completed_successfully } }, restart: unless-stopped }

  frontend:
    build:
      context: ./frontend
      args:
        NEXT_PUBLIC_API_BASE: http://localhost:8000
    ports:
      - "3000:3000"
    depends_on:
      - api
    restart: unless-stopped

volumes:
  bw_pgdata: {}
```
(If the inline-map worker syntax trips the validator, expand each worker to block form — behaviourally identical. Verify with `docker compose config`.)

- [ ] **Step 3: Write `README-docker.md`**

```markdown
# Running bellwether with Docker

1. `cp .env.docker.example .env` and edit `JWT_SECRET` / `ADMIN_PASSWORD` (and any provider keys).
2. `docker compose up --build` — starts Postgres, runs migrations, then the API (:8000), the six
   workers, and the frontend (:3000).
3. Open http://localhost:3000 and log in with the admin credentials from `.env`.

- Just the DB (for host-dev pytest): `docker compose up db`.
- Rebuild after a code change: `docker compose up --build` (only the changed layer rebuilds; deps
  are not reinstalled unless `pyproject.toml`/`package.json` changed).
- Optimize prompts: `docker compose run --rm api python -m bellwether.optimize run extract`.
- Stop: `docker compose down` (keep data) / `docker compose down -v` (drop the DB volume).
```

- [ ] **Step 4: Validate the compose file (UNSANDBOXED, no daemon needed)**

Run: `docker compose config >/dev/null && echo "compose OK"` (unsandboxed). `docker compose config` fully parses + interpolates the file (with a `.env` present — `cp .env.docker.example .env` first if needed for interpolation; do NOT commit that `.env`). Fix any schema errors (esp. the inline worker maps). Expected: prints the resolved config with no error.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.docker.example README-docker.md
git commit -m "feat(docker): full-stack compose (db + migrator + api + 6 workers + frontend) + env template + README"
```

---

### Task 4: Live bring-up smoke (host-run) + verification note

**Files:** Create `scripts/docker-smoke.sh`.

**Interfaces:** A script the USER runs on their Docker host to verify the composed stack end-to-end. (This environment can't run Docker; the script is the deliverable + the plan documents the expected output.)

- [ ] **Step 1: Write `scripts/docker-smoke.sh`**

```bash
#!/usr/bin/env bash
# Live smoke for the composed stack. Run on a machine with Docker after `docker compose up -d --build`.
set -euo pipefail
API=http://localhost:8000
WEB=http://localhost:3000
pass=0; fail=0
chk() { if [ "$1" = "$2" ]; then echo "  PASS: $3"; pass=$((pass+1)); else echo "  FAIL: $3 (got $1 want $2)"; fail=$((fail+1)); fi; }

echo "== containers up =="
for svc in db migrator api detect extract resolve measure discovery alert frontend; do
  state=$(docker compose ps --format '{{.Service}} {{.State}}' | awk -v s="$svc" '$1==s{print $2}')
  # migrator is one-shot: 'exited' with code 0 is success; others should be 'running'
  echo "  $svc: ${state:-missing}"
done

echo "== api serves openapi =="
chk "$(curl -s -o /dev/null -w '%{http_code}' $API/openapi.json)" "200" "GET $API/openapi.json"
echo "== frontend serves =="
chk "$(curl -s -o /dev/null -w '%{http_code}' $WEB/)" "200" "GET $WEB/"
echo "== CORS allows the frontend origin =="
acao=$(curl -s -D - -o /dev/null -H "Origin: http://localhost:3000" $API/leaderboard | tr -d '\r' | awk 'tolower($1)=="access-control-allow-origin:"{print $2}')
chk "$acao" "http://localhost:3000" "CORS allow-origin"
echo "== login + a write round-trips =="
TOK=$(curl -s -X POST $API/auth/token -H "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "username=${ADMIN_USERNAME:-admin}" --data-urlencode "password=${ADMIN_PASSWORD:?set ADMIN_PASSWORD}" \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
[ -n "$TOK" ] && { echo "  PASS: login"; pass=$((pass+1)); } || { echo "  FAIL: login"; fail=$((fail+1)); }
if [ -n "$TOK" ]; then
  RID=$(curl -s -X POST $API/alert_rules -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
        -d '{"name":"docker-smoke","condition":{"min_confidence":0.7},"webhook_url":null,"enabled":true}' \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
  [ -n "$RID" ] && { echo "  PASS: create rule"; pass=$((pass+1)); curl -s -o /dev/null -X DELETE $API/alert_rules/$RID -H "Authorization: Bearer $TOK"; } || { echo "  FAIL: create rule"; fail=$((fail+1)); }
fi
echo ""; echo "== $pass passed, $fail failed =="
[ "$fail" = 0 ]
```

- [ ] **Step 2: Make it executable + note the run flow**

`chmod +x scripts/docker-smoke.sh`. The intended host flow (documented, run by the user on a Docker machine):
```
cp .env.docker.example .env   # edit secrets
docker compose up -d --build  # build + start; migrator runs, api/workers/frontend start
export ADMIN_PASSWORD=...      # same as .env
./scripts/docker-smoke.sh      # expect: all PASS
```

- [ ] **Step 3: Validate the script parses (no Docker needed)**

Run: `bash -n scripts/docker-smoke.sh && echo "syntax OK"`.

- [ ] **Step 4: Commit**

```bash
git add scripts/docker-smoke.sh
git commit -m "feat(docker): host-run stack smoke test"
```

---

## Self-Review

**Spec coverage:**
- Python image (migrator+api+workers), non-root, slim, LITELLM env, layer-cached deps — Task 1 ✓ (deps-explicit variant chosen for real caching).
- Frontend image (Next standalone, browser-facing build-arg API base) — Task 2 ✓.
- Full-stack compose with the `db → migrator → {api, workers} → frontend` graph gated on `service_completed_successfully`, volume, healthchecks, `up db` still works for host-dev — Task 3 ✓.
- `.env.docker.example` (service-host DATABASE_URL, CORS, optional keys) + README — Task 3 ✓.
- Live bring-up smoke — Task 4 ✓.
- **Deferred (spec §10):** prod hardening, TLS/proxy, hot-reload dev compose, CI publish, worker replicas — no task, correct.

**Docker-not-in-sandbox flag (for the reviewer + executor):** `docker build`/`up` need the user's daemon and run UNSANDBOXED; if no daemon is reachable, tasks validate what they can offline (`docker compose config` for Task 3, `bash -n` for Task 4, `docker build --check` for Tasks 1–2) and report honestly that the full build/bring-up is the user's host step. Do NOT fake a green build.

**Two calibration points flagged in-step:** (1) the Dockerfile dep-caching variant (Task 1 Step 2 — use the deps-explicit form, keep the list synced with `pyproject.toml`); (2) the `frontend/public` COPY (Task 2 Step 3 — drop or `.gitkeep` if the dir is absent). Both are "adjust to what builds," not placeholders.

**Consistency:** `DATABASE_URL` service-host form is identical in the constraints, `.env.docker.example`, and the compose reasoning. `migrator` gate (`service_completed_successfully`) applies to api + all 6 workers uniformly. `NEXT_PUBLIC_API_BASE=http://localhost:8000` is the build arg in both `frontend/Dockerfile` and compose. `uvicorn … --factory` matches `create_app()` (verified). Alembic needs only `DATABASE_URL` (env.py reads Settings — verified, no ini edit). The 6 worker commands match the CLI `choices` (detect/extract/resolve/measure/discovery/alert — verified).
