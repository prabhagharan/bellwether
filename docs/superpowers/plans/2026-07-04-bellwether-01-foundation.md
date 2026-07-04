# bellwether Plan 1 — Foundation Implementation Plan

> **Status: ✅ Complete** — merged to `main` (2026-07-04, commit `b716de5`) via subagent-driven development. All 10 tasks implemented, task- and whole-branch-reviewed; final review's two Important findings (test isolation via `dependency_overrides`, `is_active` enforced at login) fixed. Suite: 16/16 passing, pristine. The `- [ ]` checkboxes below are left as the original plan of record; see git history for execution.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a bootable, authenticated FastAPI service on Postgres — configuration, database + migrations, JWT auth, and an env-seeded admin account — that later plans build the pipeline on.

**Architecture:** A single Python package (`bellwether`) with clear module boundaries: `config` (env settings), `db` (SQLAlchemy engine/session), `models` (ORM), `security` (password hashing, JWT, auth dependency), `api` (FastAPI app + auth routes), and `seed` (startup admin seeding). Sync SQLAlchemy 2.0 throughout (workers in later plans are plain sync processes). Alembic manages schema. Tests run against a real Postgres.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLAlchemy 2.0 (sync), Alembic, Postgres (via `psycopg[binary]`), `pydantic-settings`, `PyJWT`, `passlib[bcrypt]`, pytest.

## Global Constraints

- Python **3.11+**.
- Database is **Postgres** only. Connection via `postgresql+psycopg://…` (psycopg 3). No SQLite fallback.
- SQLAlchemy **2.0** style (`DeclarativeBase`, `Mapped`, `mapped_column`); **sync** engine/session (no async).
- JWT library is **`PyJWT`** — never `python-jose`.
- Password hashing is **`passlib[bcrypt]`**.
- Secrets/config come only from **environment variables** via `pydantic-settings` — never hardcoded, never committed. `.env` is git-ignored; `.env.example` documents keys.
- No open public registration endpoint. The only v1 account-creation path is **env seeding**.
- Every user-owned table (introduced in later plans) carries a nullable `owner_id`; in this plan only `users` exists.
- Tests use a **real Postgres** (no mocking the DB). Each test runs in a rolled-back transaction.

## File Structure

```
bellwether/
├── pyproject.toml                     # package + deps + pytest/ruff config
├── .env.example                       # documented env keys (committed)
├── .gitignore                         # ignores .env, __pycache__, etc.
├── docker-compose.yml                 # local Postgres for dev + tests
├── alembic.ini                        # Alembic config
├── migrations/
│   ├── env.py                         # Alembic environment (reads Settings, imports models)
│   └── versions/                      # migration scripts
├── src/bellwether/
│   ├── __init__.py
│   ├── config.py                      # Settings (pydantic-settings)
│   ├── db.py                          # engine, SessionLocal, get_session
│   ├── models/
│   │   ├── __init__.py                # re-exports all models for Alembic
│   │   ├── base.py                    # DeclarativeBase
│   │   └── user.py                    # User model
│   ├── security/
│   │   ├── __init__.py
│   │   ├── passwords.py               # hash_password / verify_password
│   │   ├── jwt.py                     # create_access_token / decode_token
│   │   └── deps.py                    # get_current_user dependency
│   ├── repositories/
│   │   └── users.py                   # get_user_by_username / create_user
│   ├── seed.py                        # seed_admin(session)
│   └── api/
│       ├── __init__.py
│       ├── app.py                     # create_app() factory + startup seeding
│       └── auth.py                    # /auth/token, /me routes
└── tests/
    ├── conftest.py                    # db + client fixtures
    ├── test_config.py
    ├── test_db.py
    ├── security/test_passwords.py
    ├── security/test_jwt.py
    ├── test_users_repo.py
    ├── test_seed.py
    └── api/test_auth.py
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `docker-compose.yml`, `src/bellwether/__init__.py`, `tests/__init__.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable `bellwether` package; `pytest` runnable; a local Postgres reachable at `postgresql://bellwether:bellwether@localhost:5432/bellwether`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "bellwether"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.1",
    "pydantic-settings>=2.2",
    "pyjwt>=2.8",
    "passlib[bcrypt]>=1.7",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.4"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.env
.venv/
.pytest_cache/
*.egg-info/
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Database
DATABASE_URL=postgresql+psycopg://bellwether:bellwether@localhost:5432/bellwether

# Auth
JWT_SECRET=change-me-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# Admin seeding (created on first startup if users table is empty)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
```

- [ ] **Step 4: Create `docker-compose.yml`**

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
```

- [ ] **Step 5: Create empty package files**

Create `src/bellwether/__init__.py` and `tests/__init__.py`, both empty.

- [ ] **Step 6: Install and verify**

Run:
```bash
docker compose up -d
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```
Expected: pip install succeeds; `pytest` reports "no tests ran" (exit 5) — the package imports cleanly.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore .env.example docker-compose.yml src tests
git commit -m "chore: project scaffolding, deps, local postgres"
```

---

### Task 2: Settings

**Files:**
- Create: `src/bellwether/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: env vars from Task 1's `.env.example`.
- Produces: `class Settings` with fields `database_url: str`, `jwt_secret: str`, `jwt_algorithm: str`, `jwt_expire_minutes: int`, `admin_username: str`, `admin_password: str`; and `get_settings() -> Settings` (cached).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from bellwether.config import Settings

def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("JWT_SECRET", "s3cr3t")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert s.jwt_secret == "s3cr3t"
    assert s.jwt_algorithm == "HS256"        # default
    assert s.jwt_expire_minutes == 60         # default
    assert s.admin_username == "admin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.config`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_username: str
    admin_password: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/config.py tests/test_config.py
git commit -m "feat: typed settings from environment"
```

---

### Task 3: Database engine & session

**Files:**
- Create: `src/bellwether/db.py`, `src/bellwether/models/base.py`, `src/bellwether/models/__init__.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `Base` (DeclarativeBase); `engine` (module-level Engine); `SessionLocal` (sessionmaker); `get_session()` generator yielding a `Session` (FastAPI dependency shape).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from sqlalchemy import text
from bellwether.db import SessionLocal

def test_session_executes_query():
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.db`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/models/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

```python
# src/bellwether/models/__init__.py
from bellwether.models.base import Base

__all__ = ["Base"]
```

```python
# src/bellwether/db.py
from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from bellwether.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (requires `docker compose up -d` and a valid `.env` copied from `.env.example`).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/db.py src/bellwether/models tests/test_db.py
git commit -m "feat: sqlalchemy engine, session, declarative base"
```

---

### Task 4: User model + Alembic migration

**Files:**
- Create: `src/bellwether/models/user.py`, `alembic.ini`, `migrations/env.py`, plus the generated migration in `migrations/versions/`
- Modify: `src/bellwether/models/__init__.py`

**Interfaces:**
- Consumes: `Base` from Task 3.
- Produces: `class User` with columns `id: int` (PK), `username: str` (unique, not null), `hashed_password: str` (not null), `is_active: bool` (default true), `created_at: datetime` (server default now). A `users` table created by migration.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_users_repo.py  (model-existence portion; repo added in Task 7)
from bellwether.models.user import User

def test_user_columns_exist():
    cols = set(User.__table__.columns.keys())
    assert {"id", "username", "hashed_password", "is_active", "created_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_users_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.models.user`.

- [ ] **Step 3: Write the User model**

```python
# src/bellwether/models/user.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Register the model for Alembic**

```python
# src/bellwether/models/__init__.py
from bellwether.models.base import Base
from bellwether.models.user import User

__all__ = ["Base", "User"]
```

- [ ] **Step 5: Run the model test to verify it passes**

Run: `pytest tests/test_users_repo.py -v`
Expected: PASS.

- [ ] **Step 6: Initialize Alembic**

Run: `alembic init migrations`
Then replace `migrations/env.py` with a version wired to our Settings + metadata:

```python
# migrations/env.py
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from bellwether.config import get_settings
from bellwether.models import Base  # imports all models

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 7: Generate and apply the migration**

Run:
```bash
alembic revision --autogenerate -m "create users table"
alembic upgrade head
```
Expected: a new file in `migrations/versions/`; `upgrade head` completes; the `users` table now exists.

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/models/user.py src/bellwether/models/__init__.py alembic.ini migrations
git commit -m "feat: user model and initial migration"
```

---

### Task 5: Test database fixtures

**Files:**
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: `engine`, `SessionLocal` from Task 3; migrations from Task 4.
- Produces: pytest fixture `db_session` — a `Session` bound to a transaction that is **rolled back** after each test (no state leaks). Assumes migrations already applied to the test database.

- [ ] **Step 1: Write the fixture**

```python
# tests/conftest.py
import pytest
from bellwether.db import engine
from sqlalchemy.orm import Session


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
```

- [ ] **Step 2: Add a smoke test that uses it**

```python
# append to tests/test_db.py
def test_db_session_fixture_rolls_back(db_session):
    from sqlalchemy import text
    assert db_session.execute(text("SELECT 1")).scalar() == 1
```

- [ ] **Step 3: Run to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (ensure `alembic upgrade head` has been run against the test DB).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_db.py
git commit -m "test: rolled-back db_session fixture"
```

---

### Task 6: Password hashing

**Files:**
- Create: `src/bellwether/security/__init__.py`, `src/bellwether/security/passwords.py`, `tests/security/__init__.py`, `tests/security/test_passwords.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `hash_password(plain: str) -> str`; `verify_password(plain: str, hashed: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/security/test_passwords.py
from bellwether.security.passwords import hash_password, verify_password

def test_hash_and_verify():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/security/test_passwords.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/security/__init__.py
```
```python
# src/bellwether/security/passwords.py
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
```
Also create empty `tests/security/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/security/test_passwords.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/security tests/security
git commit -m "feat: bcrypt password hashing"
```

---

### Task 7: JWT create/decode

**Files:**
- Create: `src/bellwether/security/jwt.py`, `tests/security/test_jwt.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `create_access_token(subject: str, expires_minutes: int | None = None) -> str`; `decode_token(token: str) -> dict` (raises `jwt.InvalidTokenError` on bad/expired token). Token payload uses `sub` for the username and `exp` for expiry.

- [ ] **Step 1: Write the failing test**

```python
# tests/security/test_jwt.py
import pytest, jwt
from bellwether.security.jwt import create_access_token, decode_token

def test_roundtrip():
    token = create_access_token("alice")
    payload = decode_token(token)
    assert payload["sub"] == "alice"

def test_expired_token_rejected():
    token = create_access_token("alice", expires_minutes=-1)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/security/test_jwt.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/security/jwt.py
from datetime import datetime, timedelta, timezone
import jwt
from bellwether.config import get_settings


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    minutes = settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/security/test_jwt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/security/jwt.py tests/security/test_jwt.py
git commit -m "feat: JWT access token create/decode"
```

---

### Task 8: User repository

**Files:**
- Create: `src/bellwether/repositories/__init__.py`, `src/bellwether/repositories/users.py`
- Modify: `tests/test_users_repo.py`

**Interfaces:**
- Consumes: `User` (Task 4), `Session`, `hash_password` (Task 6).
- Produces: `get_user_by_username(session, username) -> User | None`; `create_user(session, username, password) -> User` (hashes the password, flushes, returns the persisted `User`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_users_repo.py
from bellwether.repositories.users import create_user, get_user_by_username
from bellwether.security.passwords import verify_password

def test_create_and_fetch_user(db_session):
    user = create_user(db_session, "bob", "pw123")
    assert user.id is not None
    assert user.username == "bob"
    assert verify_password("pw123", user.hashed_password)
    fetched = get_user_by_username(db_session, "bob")
    assert fetched is not None and fetched.id == user.id

def test_get_missing_user_returns_none(db_session):
    assert get_user_by_username(db_session, "nobody") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_users_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.repositories.users`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/repositories/__init__.py
```
```python
# src/bellwether/repositories/users.py
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.user import User
from bellwether.security.passwords import hash_password


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()


def create_user(session: Session, username: str, password: str) -> User:
    user = User(username=username, hashed_password=hash_password(password))
    session.add(user)
    session.flush()
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_users_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/repositories tests/test_users_repo.py
git commit -m "feat: user repository (create/get)"
```

---

### Task 9: Admin env-seeding

**Files:**
- Create: `src/bellwether/seed.py`, `tests/test_seed.py`

**Interfaces:**
- Consumes: `get_settings()` (Task 2), `get_user_by_username`/`create_user` (Task 8), `Session`.
- Produces: `seed_admin(session) -> User | None` — if **no** users exist, creates the admin from `settings.admin_username`/`admin_password` and returns it; if any user already exists, returns `None` (idempotent, never creates a second time).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed.py
from sqlalchemy import select
from bellwether.seed import seed_admin
from bellwether.models.user import User
from bellwether.config import get_settings

def test_seeds_admin_when_empty(db_session, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    monkeypatch.setenv("ADMIN_PASSWORD", "rootpw")
    created = seed_admin(db_session)
    assert created is not None and created.username == "root"

def test_seed_is_idempotent(db_session):
    from bellwether.repositories.users import create_user
    create_user(db_session, "someone", "pw")
    assert seed_admin(db_session) is None
    count = db_session.execute(select(User)).scalars().all()
    assert len(count) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.seed`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/bellwether/seed.py
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.models.user import User
from bellwether.repositories.users import create_user


def seed_admin(session: Session) -> User | None:
    user_count = session.execute(select(func.count()).select_from(User)).scalar_one()
    if user_count > 0:
        return None
    settings = get_settings()
    admin = create_user(session, settings.admin_username, settings.admin_password)
    session.commit()
    return admin
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_seed.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/seed.py tests/test_seed.py
git commit -m "feat: idempotent admin env-seeding"
```

---

### Task 10: FastAPI app + auth routes

**Files:**
- Create: `src/bellwether/security/deps.py`, `src/bellwether/api/__init__.py`, `src/bellwether/api/app.py`, `src/bellwether/api/auth.py`, `tests/api/__init__.py`, `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `get_session` (Task 3), `get_user_by_username` (Task 8), `verify_password` (Task 6), `create_access_token`/`decode_token` (Task 7), `seed_admin` (Task 9).
- Produces: `create_app() -> FastAPI`; `get_current_user(...)` dependency returning the authenticated `User` (401 on missing/invalid token or inactive user); routes `POST /auth/token` (OAuth2 password form → `{access_token, token_type}`) and `GET /me` (protected → `{username}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_auth.py
import pytest
from fastapi.testclient import TestClient
from bellwether.api.app import create_app
from bellwether.db import SessionLocal
from bellwether.repositories.users import create_user

@pytest.fixture
def client():
    return TestClient(create_app())

@pytest.fixture(autouse=True)
def a_user():
    with SessionLocal() as s:
        if not __import__("bellwether.repositories.users", fromlist=["get_user_by_username"]).get_user_by_username(s, "alice"):
            create_user(s, "alice", "pw123")
            s.commit()

def test_login_returns_token(client):
    r = client.post("/auth/token", data={"username": "alice", "password": "pw123"})
    assert r.status_code == 200
    assert "access_token" in r.json()

def test_bad_password_rejected(client):
    r = client.post("/auth/token", data={"username": "alice", "password": "nope"})
    assert r.status_code == 401

def test_me_requires_token(client):
    assert client.get("/me").status_code == 401

def test_me_with_token(client):
    token = client.post("/auth/token", data={"username": "alice", "password": "pw123"}).json()["access_token"]
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "alice"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.api.app`.

- [ ] **Step 3: Write the auth dependency**

```python
# src/bellwether/security/deps.py
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.repositories.users import get_user_by_username
from bellwether.security.jwt import decode_token
from bellwether.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        username = payload.get("sub")
    except jwt.InvalidTokenError:
        raise cred_exc
    if not username:
        raise cred_exc
    user = get_user_by_username(session, username)
    if user is None or not user.is_active:
        raise cred_exc
    return user
```

- [ ] **Step 4: Write the auth routes**

```python
# src/bellwether/api/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.repositories.users import get_user_by_username
from bellwether.security.passwords import verify_password
from bellwether.security.jwt import create_access_token
from bellwether.security.deps import get_current_user
from bellwether.models.user import User

router = APIRouter()


@router.post("/auth/token")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = get_user_by_username(session, form.username)
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return {"access_token": create_access_token(user.username), "token_type": "bearer"}


@router.get("/me")
def me(current: User = Depends(get_current_user)):
    return {"username": current.username}
```

- [ ] **Step 5: Write the app factory**

```python
# src/bellwether/api/__init__.py
```
```python
# src/bellwether/api/app.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bellwether.api.auth import router as auth_router
from bellwether.db import SessionLocal
from bellwether.seed import seed_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as session:
        seed_admin(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="bellwether", lifespan=lifespan)
    app.include_router(auth_router)
    return app
```
Also create empty `tests/api/__init__.py`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/api/test_auth.py -v`
Expected: PASS (all four tests).

- [ ] **Step 7: Boot the app end-to-end (manual smoke)**

Run:
```bash
uvicorn bellwether.api.app:create_app --factory --port 8000
```
Then in another shell:
```bash
curl -s -X POST localhost:8000/auth/token -d "username=admin&password=$ADMIN_PASSWORD" | python -m json.tool
```
Expected: a JSON object with `access_token`. (The admin was seeded on startup.)

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/security/deps.py src/bellwether/api tests/api
git commit -m "feat: FastAPI app, JWT login, protected /me, startup seeding"
```

---

## Self-Review

**Spec coverage (Plan 1's slice):** Auth via PyJWT + passlib + `OAuth2PasswordBearer` (Tasks 6–10) ✓; env-seeded admin, no public registration (Task 9) ✓; secrets via `pydantic-settings`/env only (Task 2) ✓; Postgres + SQLAlchemy 2.0 sync (Tasks 3–4) ✓; `users` table (Task 4) ✓. Watchlist, connectors, LLM layer, resolve/measure, evaluation, discovery, alerts, and the Next.js app are intentionally **out of Plan 1** and scheduled for Plans 2–7.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command lists expected output.

**Type consistency:** `get_settings`, `SessionLocal`/`get_session`, `User`, `hash_password`/`verify_password`, `create_access_token`/`decode_token`, `get_user_by_username`/`create_user`, `seed_admin`, `get_current_user`, `create_app` are defined once and consumed with matching signatures across tasks.

---

## Next plans

Plans 2–7 (ingestion, LLM layer, resolve/measure, evaluation, discovery, alerts+frontend) will be written as separate documents in `docs/superpowers/plans/`, each following this same TDD, bite-sized, fully-specified structure, and each producing working, testable software.
