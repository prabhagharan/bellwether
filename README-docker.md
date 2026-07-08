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
