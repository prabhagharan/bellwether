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
