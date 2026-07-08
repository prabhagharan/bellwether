# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN python -m venv /opt/venv
# Dep layer first (cached independent of src -- a source-only change does not
# invalidate this layer), then the source + no-deps project install.
COPY pyproject.toml ./
# Keep this list in sync with the [project.dependencies] array in
# pyproject.toml. It is duplicated here (rather than `pip install .`) so that
# the dependency layer is cached independently of `src/`: a source change
# only invalidates the COPY src + pip install --no-deps . layers below.
RUN pip install --upgrade pip && pip install \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "sqlalchemy>=2.0" "alembic>=1.13" \
    "psycopg[binary]>=3.1" "pydantic-settings>=2.2" "pyjwt>=2.8" "passlib[bcrypt]>=1.7" \
    "bcrypt>=4.0,<4.1" "python-multipart>=0.0.9" "feedparser>=6.0" "dspy>=2.5" "yfinance>=0.2"
COPY src ./src
RUN pip install --no-deps .

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
# No default CMD -- compose sets `command` per service (uvicorn / worker / alembic).
