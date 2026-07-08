# bellwether — Dockerization (Compose for apps + workers) — Design Spec

**Date:** 2026-07-08
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Builds on:** the complete system (Plans 1–7b): FastAPI API, 6 worker stages, Alembic migrations, Next.js frontend, Postgres. The repo already has a db-only `docker-compose.yml`.

---

## 1. Goal

A **one-command local run**: `docker compose up` brings up the entire bellwether system — Postgres, migrations, the API, all six workers, and the frontend — wired together, reading config from a single `.env`. Good enough to also deploy to a single host. Not production-hardened (deferred), not a hot-reload dev setup (deferred).

**In scope:** two multi-stage Dockerfiles (Python for API + workers + migrator; Node for the frontend), a `.dockerignore` for each, an expanded `docker-compose.yml` (db + migrator + api + 6 workers + frontend), a `.env.docker.example` template, and a live bring-up smoke test.

**Out of scope (deferred):** pinned image digests, resource limits, a secrets manager, read-only root FS, a reverse proxy / TLS / real domain, a separate hot-reload dev compose, CI image publishing, worker scaling via `replicas`.

## 2. Processes to containerize

| Service | Image | Command | Kind |
|---|---|---|---|
| `db` | `postgres:16` | (default) | long-running + volume |
| `migrator` | Python | `alembic upgrade head` | one-shot (runs, exits 0) |
| `api` | Python | `uvicorn bellwether.api.app:create_app --factory --host 0.0.0.0 --port 8000` | long-running, port 8000 |
| `detect`/`extract`/`resolve`/`measure`/`discovery`/`alert` | Python (same image) | `python -m bellwether.worker <stage>` | 6 long-running workers |
| `frontend` | Node | `next start -p 3000` | long-running, port 3000 |

**Two images total:** the Python image is built once and reused by the migrator + API + all six workers (they differ only by the compose `command`); the Node image is the built frontend.

## 3. Startup ordering (correctness-critical)

The migrator is the single gate that guarantees the schema is current before any process reads/writes it.

```
db (healthcheck: pg_isready)
   └─ migrator   depends_on: db (service_healthy)          → alembic upgrade head → exit 0
        └─ api + 6 workers   depends_on: migrator (service_completed_successfully)
             └─ frontend     depends_on: api (service_started)
```

- `db`: `healthcheck` using `pg_isready -U bellwether`; a named volume `bw_pgdata:/var/lib/postgresql/data` for persistence.
- `migrator`: waits for `db` healthy, runs `alembic upgrade head`, exits. Migrations are **incremental** (Alembic's `alembic_version` table tracks the applied revision; already-at-head is a no-op) — never a rebuild, data preserved across upgrades. `restart: "no"` (one-shot).
- `api` + the 6 workers: `depends_on: { migrator: { condition: service_completed_successfully } }` — nothing touches the schema until migrations finish; workers never race an un-migrated DB. `restart: unless-stopped`.
- `frontend`: `depends_on: api` (started). `restart: unless-stopped`.

## 4. Dockerfiles

### 4.1 `Dockerfile` (Python — migrator + api + workers)
Multi-stage, slim runtime, layer-ordered so **code changes rebuild in seconds and deps only reinstall when `pyproject.toml` changes**.
- **builder** (`python:3.11-slim`): create a venv; `COPY pyproject.toml` → `pip install .` (dep layer, cached) → `COPY src/ ./src/` → install the package.
- **runtime** (`python:3.11-slim`): copy the venv + `src/` + `alembic.ini` + `migrations/` only — no build toolchain, no `.git`, no tests. Create + run as a **non-root** user. Set `ENV LITELLM_LOCAL_MODEL_COST_MAP=True` (so dspy-importing workers never hang on litellm's cost-map fetch) and `PATH` to the venv. **No meaningful default `CMD`** — every service overrides `command` in compose.
- Note: dspy + yfinance pull heavy transitive deps → the first build takes a few minutes; subsequently the dep layer is cached.

### 4.2 `frontend/Dockerfile` (Node — built SPA)
Multi-stage.
- **builder** (`node:22-slim`): `COPY package.json package-lock.json` → `npm ci` (reproducible via the committed lockfile) → `COPY` the rest → `npm run build`. Takes a **build arg** `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`) exported into the build env, because `NEXT_PUBLIC_*` is inlined into the client bundle at build time (see §5).
- **runtime** (`node:22-slim`): copy the build output + `node_modules` (or use Next standalone output); run `next start -p 3000` as non-root. This serves the **production build**, not `next dev`.

### 4.3 `.dockerignore` (root and `frontend/`)
Root excludes: `.venv`, `__pycache__`, `.git`, `tests`, `.pytest_cache`, `*.pyc`, `docs`, `.superpowers`, `frontend` (the Python image doesn't need it), `.env`. Frontend excludes: `node_modules`, `.next`, `.git`, `openapi.json`. Keeps the build context small and reproducible.

## 5. Config & the browser-facing frontend URL

**Single `.env` at the repo root**, consumed by the Python services via compose `env_file:` (matches `Settings(env_file=".env")`). Ship a **`.env.docker.example`** template; two values differ from host-dev:

- **`DATABASE_URL` uses the service hostname:** `postgresql+psycopg://bellwether:bellwether@db:5432/bellwether` — inside the compose network Postgres is `db`, not `localhost`. (Host-dev `.env` uses `localhost:5432`; that's why the docker template is separate.)
- **`CORS_ORIGINS=["http://localhost:3000"]`** so the browser-loaded frontend can call the API (supported since Plan 7a).
- Pass-through: `JWT_SECRET`, `ADMIN_USERNAME`/`ADMIN_PASSWORD`, and the optional provider keys `ANTHROPIC_API_KEY` / `TAVILY_API_KEY` / `X_API_KEY` (workers read them from the environment; keyless-degrade handles absence).

**The gotcha — the frontend's API URL is browser-facing, not container-facing.** The frontend is a client-rendered SPA: the **user's browser** makes the API calls, not the `frontend` container. So `NEXT_PUBLIC_API_BASE` must be a URL the browser can reach — **`http://localhost:8000`** (the host-published API port) — **not** `http://api:8000` (the in-container service name the browser can't resolve). Because `NEXT_PUBLIC_*` is inlined at **build time**, it's a **build arg** to `frontend/Dockerfile`, not a runtime env var. Behind a real domain later, rebuild the frontend with that domain.

Network summary: API↔DB and workers↔DB talk over the compose network by service name (`db`); browser↔API talks over `localhost:8000`; CORS bridges the two origins.

## 6. `docker-compose.yml`

Expands the current db-only file into the full stack. **`docker compose up db` still starts just Postgres for host-dev pytest** (the published `localhost:5432` mapping is unchanged), so the existing test workflow keeps working; `docker compose up` runs everything.

- `db`: `postgres:16`, env (user/password/db), volume `bw_pgdata`, `ports: 5432:5432` (host-dev + browser-independent), healthcheck.
- `migrator`: `build: .`, `command: alembic upgrade head`, `env_file: .env`, `depends_on: db healthy`, `restart: "no"`.
- `api`: `build: .`, uvicorn command, `env_file: .env`, `ports: 8000:8000`, `depends_on: migrator completed`, healthcheck (`GET /openapi.json`), `restart: unless-stopped`.
- `detect`/`extract`/`resolve`/`measure`/`discovery`/`alert`: `build: .`, `command: python -m bellwether.worker <stage>`, `env_file: .env`, `depends_on: migrator completed`, `restart: unless-stopped`. (The `build: .` is shared — Compose builds the Python image once and reuses it across these services.)
- `frontend`: `build: { context: ./frontend, args: { NEXT_PUBLIC_API_BASE: http://localhost:8000 } }`, `ports: 3000:3000`, `depends_on: api`, `restart: unless-stopped`.
- top-level `volumes: { bw_pgdata: {} }`.

## 7. Upgrade model (how a rebuild behaves)

- **DB upgrade:** `docker compose up` re-runs the migrator → `alembic upgrade head` applies only new revisions (incremental, idempotent, data preserved in `bw_pgdata`).
- **Code change:** `docker compose up --build` → Docker rebuilds only from the `COPY src/` layer down; the cached dependency layer is reused (**deps NOT reinstalled**). Seconds.
- **Dependency change** (`pyproject.toml` / frontend `package.json`): the dep layer rebuilds → pip/npm reinstall. Only then.
- **No `--build`:** existing images are reused, no rebuild.

## 8. Verification (live bring-up — Docker not runnable in this sandbox)

Run on the host (unsandboxed / user's Docker daemon):
1. `docker compose config` — validates syntax + env interpolation.
2. `docker compose build` — both images build cleanly.
3. `docker compose up -d` — stack comes up: `migrator` exits 0; `api` healthcheck green; the 6 workers stay running (log a claim-loop, connected to `db`); `frontend` serves.
4. **Smoke** (a script, reusing the existing e2e harness re-pointed at the composed stack): `GET localhost:8000/openapi.json` → 200; `GET localhost:3000` → 200; login via `/auth/token`; a write round-trips (create/list/delete an alert rule); each worker container is `Up`. Self-cleaning.
5. `docker compose down` (keep the volume) / `down -v` (drop data).

The plan provides the exact commands + the smoke script; the user runs the bring-up (this environment can't run Docker).

## 9. File structure (informs the plan)

```
/
├── Dockerfile               # (new) Python: migrator + api + workers
├── .dockerignore            # (new)
├── docker-compose.yml       # (modify) db-only -> full stack
├── .env.docker.example      # (new) container config template
├── frontend/
│   ├── Dockerfile           # (new) Node: built SPA
│   └── .dockerignore        # (new)
└── (README note: `docker compose up`)
```

## 10. Deferred with intent

- Production hardening: pinned digests, resource limits, read-only FS, a secrets manager.
- Reverse proxy / TLS / real domain (would rebuild the frontend with a domain build-arg).
- A separate hot-reload dev compose (bind-mounts + `--reload` / `next dev`).
- CI image build/publish to a registry.
- Worker scaling via `deploy.replicas` / a shared claimable queue (the SKIP-LOCKED harness already supports concurrent workers).
