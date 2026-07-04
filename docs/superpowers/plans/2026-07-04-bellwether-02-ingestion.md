# bellwether Plan 2 — Ingestion & Watchlist Implementation Plan

> **Status: ✅ Complete** — merged to `main` (2026-07-04) via subagent-driven development. All 8 tasks implemented, task- and whole-branch-reviewed. Post-merge, manual end-to-end testing caught a real bug (`get_session` never committed → API writes didn't persist), fixed in `750dad9`; test isolation hardened so a shared dev DB can't cause spurious failures. Suite: 33/33 passing; verified live (watchlist → ingest → statements). Checkboxes left as the original plan of record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user manage a watchlist of figures and their sources via the authenticated API, and ingest real statements from RSS sources into a deduplicated, timestamped `statements` table.

**Architecture:** Builds on Plan 1 (FastAPI + Postgres + JWT). Adds three tables (`figures`, `sources`, `statements`); a pluggable connector layer (`RawItem` + `SourceConnector` protocol + an RSS connector + a registry); an ingestor that fetches via a source's connector and persists deduped statements; owner-scoped watchlist repositories + API routes; and a statements-listing API. The generic status-claiming worker/queue (`FOR UPDATE SKIP LOCKED`) is intentionally deferred to Plan 3, where the first status-consuming worker (Detect) lives — the ingestor here is a straightforward poller, not a queue consumer.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (sync), Alembic, Postgres (psycopg 3, JSONB), `feedparser`, pydantic v2, pytest.

## Global Constraints

- Python **3.11+**. Postgres only via `postgresql+psycopg://` (psycopg 3). SQLAlchemy **2.0** sync (`Mapped`, `mapped_column`).
- JSONB columns via `sqlalchemy.dialects.postgresql.JSONB`.
- **User-owned tables carry a nullable `owner_id` FK to `users.id`:** `figures`, `sources`. **`statements` is shared corpus — NO `owner_id`** (per the spec's future subscription model, statement/detection/extraction/impact rows are global).
- Watchlist API routes are **authenticated** (`get_current_user`); create sets `owner_id = current_user.id`; list/get/delete are **owner-scoped** to `current_user.id`.
- **Read-only ingestion:** connectors only fetch already-published content; every statement stores its source URL and a provenance level (`primary` | `reported`) copied from its source.
- **Precise `published_at` is load-bearing** and timezone-aware (UTC). RSS timestamps parsed as UTC via `calendar.timegm` (NOT `time.mktime`, which assumes local time).
- **Dedup:** a `statements` row is unique on `(source_id, external_id)`; re-ingesting the same item creates no duplicate.
- **Connector types** are `rss` | `x` | `youtube` | `news`; only **`rss`** is implemented in this plan. `build_connector` raises `UnknownConnectorType` for the others; `run_ingest_pass` skips sources whose type is unimplemented rather than crashing.
- Tests use a **real Postgres** (no DB mocking). API tests run through the transactional `db_session` fixture via `app.dependency_overrides[get_session]` (the Plan 1 convention); connector tests use a local fixture feed file — **no network in tests**.
- ENVIRONMENT: the shell's `python`/`pytest`/`alembic` are shadowed by a MacPorts python3.12; run everything via `.venv/bin/python -m …` (and `.venv/bin/alembic …`). Postgres up via `docker compose up -d`; `.env` exists (git-ignored).

## File Structure

```
src/bellwether/
├── models/
│   ├── __init__.py                 # (modify) register Figure, Source, Statement
│   ├── figure.py                   # Figure model
│   ├── source.py                   # Source model
│   └── statement.py                # Statement model
├── connectors/
│   ├── __init__.py
│   ├── base.py                     # RawItem dataclass + SourceConnector Protocol
│   ├── rss.py                      # RssConnector (feedparser)
│   └── registry.py                 # build_connector(source) + UnknownConnectorType
├── repositories/
│   ├── watchlist.py                # figures + sources CRUD (owner-scoped)
│   └── statements.py               # list_statements(...)
├── ingest.py                       # ingest_source(...) + run_ingest_pass(...)
└── api/
    ├── schemas.py                  # pydantic request/response models
    ├── watchlist.py                # /figures + /sources routes
    ├── statements.py               # /statements route
    └── app.py                      # (modify) include the two new routers
migrations/versions/                # one new migration (figures, sources, statements)
tests/
├── fixtures/sample_feed.xml        # local RSS fixture (no network)
├── connectors/test_rss.py
├── test_ingest.py
├── test_watchlist_repo.py
├── test_statements_repo.py
└── api/
    ├── conftest.py                 # shared `client` + `auth_headers` fixtures
    ├── test_watchlist_api.py
    └── test_statements_api.py
```

---

### Task 1: Models + migration (figures, sources, statements)

**Files:**
- Create: `src/bellwether/models/figure.py`, `src/bellwether/models/source.py`, `src/bellwether/models/statement.py`
- Modify: `src/bellwether/models/__init__.py`, `pyproject.toml` (add `feedparser`)
- Create: the generated migration under `migrations/versions/`
- Test: `tests/test_models_ingestion.py`

**Interfaces:**
- Consumes: `Base` (`bellwether.models.base`), `User` (for FK target).
- Produces:
  - `Figure` (`figures`): `id` PK, `name` str, `type` str, `aliases` JSONB (list, default `[]`), `owner_id` nullable FK→users.id, `created_at`.
  - `Source` (`sources`): `id` PK, `figure_id` FK→figures.id (CASCADE, indexed), `connector_type` str, `config` JSONB (dict, default `{}`), `provenance` str default `"primary"`, `origin` str default `"manual"`, `enabled` bool default `True`, `poll_interval_seconds` int default `300`, `last_polled_at` nullable tz datetime, `owner_id` nullable FK→users.id, `created_at`.
  - `Statement` (`statements`): `id` PK, `figure_id` FK→figures.id (indexed), `source_id` FK→sources.id (CASCADE, indexed), `external_id` str, `text` Text, `url` nullable str, `provenance` str, `published_at` tz datetime (indexed), `ingested_at` tz datetime server default now(), `status` str default `"new"` (indexed). Unique constraint `uq_statements_source_external` on `(source_id, external_id)`. **No `owner_id`.**

- [ ] **Step 1: Add feedparser dependency**

In `pyproject.toml`, add `"feedparser>=6.0",` to `[project].dependencies` (after the existing entries), then install:
```bash
.venv/bin/python -m pip install -e ".[dev]"
```
Expected: feedparser installs.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_models_ingestion.py
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement

def test_ingestion_models_columns():
    assert {"id", "name", "type", "aliases", "owner_id", "created_at"} <= set(Figure.__table__.columns.keys())
    assert {"id", "figure_id", "connector_type", "config", "provenance", "origin",
            "enabled", "poll_interval_seconds", "last_polled_at", "owner_id", "created_at"} <= set(Source.__table__.columns.keys())
    stmt_cols = set(Statement.__table__.columns.keys())
    assert {"id", "figure_id", "source_id", "external_id", "text", "url",
            "provenance", "published_at", "ingested_at", "status"} <= stmt_cols
    assert "owner_id" not in stmt_cols  # statements are shared corpus
    # dedup uniqueness on (source_id, external_id)
    uniques = [c for c in Statement.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"source_id", "external_id"} for u in uniques)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models_ingestion.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.models.figure`.

- [ ] **Step 4: Write the Figure model**

```python
# src/bellwether/models/figure.py
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Figure(Base):
    __tablename__ = "figures"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: Write the Source model**

```python
# src/bellwether/models/source.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    figure_id: Mapped[int] = mapped_column(
        ForeignKey("figures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 6: Write the Statement model**

```python
# src/bellwether/models/statement.py
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_statements_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    figure_id: Mapped[int] = mapped_column(ForeignKey("figures.id"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", index=True)
```

- [ ] **Step 7: Register the models for Alembic**

```python
# src/bellwether/models/__init__.py
from bellwether.models.base import Base
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement

__all__ = ["Base", "User", "Figure", "Source", "Statement"]
```

- [ ] **Step 8: Run the model test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models_ingestion.py -v`
Expected: PASS.

- [ ] **Step 9: Generate and apply the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "create figures, sources, statements"
.venv/bin/alembic upgrade head
```
Expected: a new version file created; `upgrade head` completes; the three tables exist. Open the generated file and confirm it creates all three tables, the FKs, the indexes, and the `uq_statements_source_external` unique constraint (autogenerate produces these from the models). Do not hand-edit beyond what autogenerate produced.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/bellwether/models tests/test_models_ingestion.py migrations
git commit -m "feat: figures/sources/statements models and migration"
```

---

### Task 2: Connector base — RawItem + SourceConnector protocol

**Files:**
- Create: `src/bellwether/connectors/__init__.py` (empty), `src/bellwether/connectors/base.py`
- Test: `tests/connectors/__init__.py` (empty), `tests/connectors/test_base.py`

**Interfaces:**
- Produces:
  - `RawItem` — frozen dataclass with fields `external_id: str`, `text: str`, `url: str | None`, `published_at: datetime`.
  - `SourceConnector` — a `typing.Protocol` with method `fetch(self) -> list[RawItem]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/connectors/test_base.py
from datetime import datetime, timezone
from bellwether.connectors.base import RawItem, SourceConnector

def test_rawitem_holds_fields():
    ts = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    item = RawItem(external_id="abc", text="hello", url="https://x/y", published_at=ts)
    assert item.external_id == "abc"
    assert item.text == "hello"
    assert item.url == "https://x/y"
    assert item.published_at == ts

def test_connector_protocol_is_runtime_checkable():
    class Dummy:
        def fetch(self):
            return []
    assert isinstance(Dummy(), SourceConnector)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/connectors/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.connectors.base`.

- [ ] **Step 3: Write the implementation**

Create empty `src/bellwether/connectors/__init__.py` and empty `tests/connectors/__init__.py`, then:
```python
# src/bellwether/connectors/base.py
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RawItem:
    external_id: str
    text: str
    url: str | None
    published_at: datetime


@runtime_checkable
class SourceConnector(Protocol):
    def fetch(self) -> list[RawItem]:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/connectors/test_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/connectors/__init__.py src/bellwether/connectors/base.py tests/connectors
git commit -m "feat: connector base (RawItem + SourceConnector protocol)"
```

---

### Task 3: RSS connector

**Files:**
- Create: `src/bellwether/connectors/rss.py`, `tests/fixtures/sample_feed.xml`
- Test: `tests/connectors/test_rss.py`

**Interfaces:**
- Consumes: `RawItem` (Task 2), `feedparser`.
- Produces: `RssConnector` — constructed with `RssConnector(feed_url: str)`; `fetch() -> list[RawItem]` parses the feed (URL, file path, or raw XML — `feedparser.parse` accepts all three). Each entry maps to a `RawItem`: `external_id` = entry `id`/`guid`/`link` (first present); `text` = title, plus `"\n\n" + summary` if a non-empty summary exists; `url` = entry link; `published_at` = entry `published_parsed`/`updated_parsed` converted to tz-aware UTC via `calendar.timegm`. Entries lacking both an id/guid/link and a timestamp are skipped.

- [ ] **Step 1: Create the fixture feed**

```xml
<!-- tests/fixtures/sample_feed.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Speeches</title>
    <link>https://example.test/feed</link>
    <item>
      <title>Rates will stay higher for longer</title>
      <description>The chair signaled a restrictive stance.</description>
      <link>https://example.test/speech-1</link>
      <guid>speech-0001</guid>
      <pubDate>Fri, 04 Jul 2026 14:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Inflation is easing</title>
      <description>Progress toward the target.</description>
      <link>https://example.test/speech-2</link>
      <guid>speech-0002</guid>
      <pubDate>Fri, 04 Jul 2026 16:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Write the failing test**

```python
# tests/connectors/test_rss.py
from datetime import datetime, timezone
from pathlib import Path
from bellwether.connectors.rss import RssConnector

FEED = str(Path(__file__).parent.parent / "fixtures" / "sample_feed.xml")

def test_rss_fetch_maps_entries():
    items = RssConnector(FEED).fetch()
    assert len(items) == 2
    first = next(i for i in items if i.external_id == "speech-0001")
    assert "Rates will stay higher for longer" in first.text
    assert "restrictive stance" in first.text
    assert first.url == "https://example.test/speech-1"
    assert first.published_at == datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc)

def test_rss_published_at_is_utc_not_local():
    # 14:30 GMT must parse to exactly 14:30 UTC regardless of the machine's local tz
    first = next(i for i in RssConnector(FEED).fetch() if i.external_id == "speech-0001")
    assert first.published_at.tzinfo is not None
    assert first.published_at.hour == 14 and first.published_at.minute == 30
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/connectors/test_rss.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.connectors.rss`.

- [ ] **Step 4: Write the RSS connector**

```python
# src/bellwether/connectors/rss.py
import calendar
from datetime import datetime, timezone
import feedparser
from bellwether.connectors.base import RawItem


class RssConnector:
    def __init__(self, feed_url: str):
        self.feed_url = feed_url

    def fetch(self) -> list[RawItem]:
        parsed = feedparser.parse(self.feed_url)
        items: list[RawItem] = []
        for entry in parsed.entries:
            external_id = entry.get("id") or entry.get("guid") or entry.get("link")
            tstruct = entry.get("published_parsed") or entry.get("updated_parsed")
            if not external_id or not tstruct:
                continue
            published_at = datetime.fromtimestamp(calendar.timegm(tstruct), tz=timezone.utc)
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title}\n\n{summary}" if summary else title
            items.append(
                RawItem(
                    external_id=str(external_id),
                    text=text,
                    url=entry.get("link"),
                    published_at=published_at,
                )
            )
        return items
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/connectors/test_rss.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/connectors/rss.py tests/fixtures/sample_feed.xml tests/connectors/test_rss.py
git commit -m "feat: RSS connector (feedparser, UTC timestamps)"
```

---

### Task 4: Watchlist repository (figures + sources, owner-scoped)

**Files:**
- Create: `src/bellwether/repositories/watchlist.py`
- Test: `tests/test_watchlist_repo.py`

**Interfaces:**
- Consumes: `Figure`, `Source` (Task 1), `Session`.
- Produces (all owner-scoped; `owner_id` may be an int or `None`):
  - `create_figure(session, name, type, aliases, owner_id) -> Figure`
  - `list_figures(session, owner_id) -> list[Figure]`
  - `get_figure(session, figure_id, owner_id) -> Figure | None`
  - `delete_figure(session, figure_id, owner_id) -> bool`
  - `add_source(session, figure_id, connector_type, config, provenance, origin, owner_id) -> Source | None` (returns `None` if the figure is not owned by `owner_id`)
  - `list_sources(session, figure_id, owner_id) -> list[Source]`
  - `get_source(session, source_id, owner_id) -> Source | None`
  - `set_source_enabled(session, source_id, enabled, owner_id) -> Source | None`
  - `delete_source(session, source_id, owner_id) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watchlist_repo.py
from bellwether.repositories.watchlist import (
    create_figure, list_figures, get_figure, delete_figure,
    add_source, list_sources, get_source, set_source_enabled, delete_source,
)

def test_figure_crud_is_owner_scoped(db_session):
    f = create_figure(db_session, "Jerome Powell", "central_bank", ["Powell"], owner_id=1)
    db_session.flush()
    assert f.id is not None
    assert [x.id for x in list_figures(db_session, owner_id=1)] == [f.id]
    assert list_figures(db_session, owner_id=2) == []          # other owner sees nothing
    assert get_figure(db_session, f.id, owner_id=2) is None     # cannot read across owners
    assert get_figure(db_session, f.id, owner_id=1).name == "Jerome Powell"
    assert delete_figure(db_session, f.id, owner_id=2) is False # cannot delete across owners
    assert delete_figure(db_session, f.id, owner_id=1) is True

def test_source_crud_is_owner_scoped(db_session):
    f = create_figure(db_session, "ECB", "central_bank", [], owner_id=1)
    db_session.flush()
    s = add_source(db_session, f.id, "rss", {"feed_url": "https://x/feed"}, "primary", "manual", owner_id=1)
    db_session.flush()
    assert s is not None and s.figure_id == f.id and s.enabled is True
    # cannot add a source to a figure you don't own
    assert add_source(db_session, f.id, "rss", {"feed_url": "https://x"}, "primary", "manual", owner_id=2) is None
    assert [x.id for x in list_sources(db_session, f.id, owner_id=1)] == [s.id]
    assert get_source(db_session, s.id, owner_id=2) is None
    updated = set_source_enabled(db_session, s.id, False, owner_id=1)
    assert updated.enabled is False
    assert delete_source(db_session, s.id, owner_id=2) is False
    assert delete_source(db_session, s.id, owner_id=1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_watchlist_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.repositories.watchlist`.

- [ ] **Step 3: Write the repository**

```python
# src/bellwether/repositories/watchlist.py
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.figure import Figure
from bellwether.models.source import Source


def create_figure(session: Session, name: str, type: str, aliases: list, owner_id: int | None) -> Figure:
    figure = Figure(name=name, type=type, aliases=aliases, owner_id=owner_id)
    session.add(figure)
    session.flush()
    return figure


def list_figures(session: Session, owner_id: int | None) -> list[Figure]:
    return list(
        session.execute(select(Figure).where(Figure.owner_id == owner_id).order_by(Figure.id)).scalars()
    )


def get_figure(session: Session, figure_id: int, owner_id: int | None) -> Figure | None:
    return session.execute(
        select(Figure).where(Figure.id == figure_id, Figure.owner_id == owner_id)
    ).scalar_one_or_none()


def delete_figure(session: Session, figure_id: int, owner_id: int | None) -> bool:
    figure = get_figure(session, figure_id, owner_id)
    if figure is None:
        return False
    session.delete(figure)
    session.flush()
    return True


def add_source(session: Session, figure_id: int, connector_type: str, config: dict,
               provenance: str, origin: str, owner_id: int | None) -> Source | None:
    if get_figure(session, figure_id, owner_id) is None:
        return None
    source = Source(
        figure_id=figure_id, connector_type=connector_type, config=config,
        provenance=provenance, origin=origin, owner_id=owner_id,
    )
    session.add(source)
    session.flush()
    return source


def list_sources(session: Session, figure_id: int, owner_id: int | None) -> list[Source]:
    return list(
        session.execute(
            select(Source).where(Source.figure_id == figure_id, Source.owner_id == owner_id).order_by(Source.id)
        ).scalars()
    )


def get_source(session: Session, source_id: int, owner_id: int | None) -> Source | None:
    return session.execute(
        select(Source).where(Source.id == source_id, Source.owner_id == owner_id)
    ).scalar_one_or_none()


def set_source_enabled(session: Session, source_id: int, enabled: bool, owner_id: int | None) -> Source | None:
    source = get_source(session, source_id, owner_id)
    if source is None:
        return None
    source.enabled = enabled
    session.flush()
    return source


def delete_source(session: Session, source_id: int, owner_id: int | None) -> bool:
    source = get_source(session, source_id, owner_id)
    if source is None:
        return False
    session.delete(source)
    session.flush()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_watchlist_repo.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/repositories/watchlist.py tests/test_watchlist_repo.py
git commit -m "feat: owner-scoped watchlist repository (figures + sources)"
```

---

### Task 5: Statements repository (list with filters)

**Files:**
- Create: `src/bellwether/repositories/statements.py`
- Test: `tests/test_statements_repo.py`

**Interfaces:**
- Consumes: `Statement` (Task 1), `Figure`, `Source`, `Session`.
- Produces: `list_statements(session, figure_id=None, status=None, limit=50) -> list[Statement]` — newest first by `published_at`, optionally filtered by `figure_id` and/or `status`, capped at `limit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_statements_repo.py
from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.repositories.statements import list_statements

def _seed(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    for i, (ext, when, st) in enumerate([
        ("a", datetime(2026, 7, 1, tzinfo=timezone.utc), "new"),
        ("b", datetime(2026, 7, 3, tzinfo=timezone.utc), "new"),
        ("c", datetime(2026, 7, 2, tzinfo=timezone.utc), "extracted"),
    ]):
        db_session.add(Statement(figure_id=f.id, source_id=s.id, external_id=ext, text=ext,
                                 url=None, provenance="primary", published_at=when, status=st))
    db_session.flush()
    return f

def test_list_orders_newest_first_and_filters(db_session):
    f = _seed(db_session)
    all_stmts = list_statements(db_session, figure_id=f.id)
    assert [s.external_id for s in all_stmts] == ["b", "c", "a"]   # newest published_at first
    new_only = list_statements(db_session, figure_id=f.id, status="new")
    assert {s.external_id for s in new_only} == {"a", "b"}

def test_list_respects_limit(db_session):
    f = _seed(db_session)
    assert len(list_statements(db_session, figure_id=f.id, limit=1)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_statements_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.repositories.statements`.

- [ ] **Step 3: Write the repository**

```python
# src/bellwether/repositories/statements.py
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement


def list_statements(session: Session, figure_id: int | None = None,
                    status: str | None = None, limit: int = 50) -> list[Statement]:
    query = select(Statement)
    if figure_id is not None:
        query = query.where(Statement.figure_id == figure_id)
    if status is not None:
        query = query.where(Statement.status == status)
    query = query.order_by(Statement.published_at.desc()).limit(limit)
    return list(session.execute(query).scalars())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_statements_repo.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/repositories/statements.py tests/test_statements_repo.py
git commit -m "feat: statements repository (list with filters)"
```

---

### Task 6: Ingestor + connector registry

**Files:**
- Create: `src/bellwether/connectors/registry.py`, `src/bellwether/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `Source`, `Statement` (Task 1), `RssConnector` (Task 3), `SourceConnector` (Task 2), `Session`.
- Produces:
  - `registry.UnknownConnectorType(Exception)`.
  - `registry.build_connector(source: Source) -> SourceConnector` — for `connector_type == "rss"` returns `RssConnector(source.config["feed_url"])`; otherwise raises `UnknownConnectorType`.
  - `ingest.ingest_source(session, source) -> list[Statement]` — fetches via the source's connector, dedups against existing `(source_id, external_id)`, inserts new `Statement`s (`provenance` copied from source, `status="new"`), sets `source.last_polled_at` to now (UTC), flushes, returns the new statements.
  - `ingest.run_ingest_pass(session) -> int` — iterates all `enabled` sources, calls `ingest_source`, skips any source whose connector type is unimplemented (`UnknownConnectorType`), commits, returns the total count of new statements.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
from pathlib import Path
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.connectors.registry import build_connector, UnknownConnectorType
from bellwether.ingest import ingest_source, run_ingest_pass

FEED = str(Path(__file__).parent / "fixtures" / "sample_feed.xml")

def _rss_source(db_session, enabled=True):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": FEED},
               provenance="primary", origin="manual", owner_id=1, enabled=enabled)
    db_session.add(s); db_session.flush()
    return s

def test_ingest_source_creates_deduped_statements(db_session):
    s = _rss_source(db_session)
    new1 = ingest_source(db_session, s)
    assert len(new1) == 2
    assert {st.external_id for st in new1} == {"speech-0001", "speech-0002"}
    assert all(st.status == "new" and st.provenance == "primary" for st in new1)
    assert s.last_polled_at is not None
    # re-ingesting the same feed creates no duplicates
    new2 = ingest_source(db_session, s)
    assert new2 == []
    total = db_session.execute(select(func.count()).select_from(Statement)).scalar_one()
    assert total == 2

def test_build_connector_rejects_unknown_type(db_session):
    f = Figure(name="X", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="carrier_pigeon", config={}, provenance="primary",
               origin="manual", owner_id=1, enabled=True)
    db_session.add(s); db_session.flush()
    try:
        build_connector(s)
        assert False, "expected UnknownConnectorType"
    except UnknownConnectorType:
        pass

def test_run_ingest_pass_skips_unimplemented_and_disabled(db_session):
    rss = _rss_source(db_session)                       # enabled rss -> 2 statements
    f2 = Figure(name="Y", type="individual", aliases=[], owner_id=1); db_session.add(f2); db_session.flush()
    db_session.add(Source(figure_id=f2.id, connector_type="x", config={}, provenance="primary",
                          origin="manual", owner_id=1, enabled=True))   # unimplemented -> skipped
    disabled = _rss_source(db_session, enabled=False)                    # disabled -> skipped
    db_session.flush()
    count = run_ingest_pass(db_session)
    assert count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.connectors.registry`.

- [ ] **Step 3: Write the registry**

```python
# src/bellwether/connectors/registry.py
from bellwether.connectors.base import SourceConnector
from bellwether.connectors.rss import RssConnector
from bellwether.models.source import Source


class UnknownConnectorType(Exception):
    pass


def build_connector(source: Source) -> SourceConnector:
    if source.connector_type == "rss":
        return RssConnector(source.config["feed_url"])
    raise UnknownConnectorType(source.connector_type)
```

- [ ] **Step 4: Write the ingestor**

```python
# src/bellwether/ingest.py
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.connectors.registry import build_connector, UnknownConnectorType
from bellwether.models.source import Source
from bellwether.models.statement import Statement


def ingest_source(session: Session, source: Source) -> list[Statement]:
    connector = build_connector(source)
    items = connector.fetch()
    existing = set(
        session.execute(
            select(Statement.external_id).where(Statement.source_id == source.id)
        ).scalars()
    )
    new_statements: list[Statement] = []
    for item in items:
        if item.external_id in existing:
            continue
        statement = Statement(
            figure_id=source.figure_id,
            source_id=source.id,
            external_id=item.external_id,
            text=item.text,
            url=item.url,
            provenance=source.provenance,
            published_at=item.published_at,
            status="new",
        )
        session.add(statement)
        new_statements.append(statement)
        existing.add(item.external_id)
    source.last_polled_at = datetime.now(timezone.utc)
    session.flush()
    return new_statements


def run_ingest_pass(session: Session) -> int:
    sources = session.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    total = 0
    for source in sources:
        try:
            total += len(ingest_source(session, source))
        except UnknownConnectorType:
            continue
    session.commit()
    return total
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/connectors/registry.py src/bellwether/ingest.py tests/test_ingest.py
git commit -m "feat: connector registry + ingestor with dedup"
```

---

### Task 7: Watchlist API (figures + sources) + schemas

**Files:**
- Create: `src/bellwether/api/schemas.py`, `src/bellwether/api/watchlist.py`, `tests/api/conftest.py`, `tests/api/test_watchlist_api.py`
- Modify: `src/bellwether/api/app.py` (include the watchlist router)

**Interfaces:**
- Consumes: `get_session` (`bellwether.db`), `get_current_user` (`bellwether.security.deps`), the watchlist repository (Task 4), `User`.
- Produces:
  - `schemas.py`: `FigureCreate{name,type,aliases=[]}`, `FigureRead{id,name,type,aliases}`, `SourceCreate{connector_type,config,provenance="primary"}`, `SourceRead{id,figure_id,connector_type,config,provenance,origin,enabled}`, `SourceUpdate{enabled}` — Read models use `model_config = ConfigDict(from_attributes=True)`.
  - `watchlist.py` router, all `Depends(get_current_user)`:
    - `POST /figures` → 201 `FigureRead`
    - `GET /figures` → `list[FigureRead]`
    - `DELETE /figures/{figure_id}` → 204; 404 if not owned
    - `POST /figures/{figure_id}/sources` → 201 `SourceRead`; 404 if figure not owned; 422 if `connector_type=="rss"` and `config` lacks `feed_url`
    - `GET /figures/{figure_id}/sources` → `list[SourceRead]`; 404 if figure not owned
    - `PATCH /sources/{source_id}` (body `SourceUpdate`) → `SourceRead`; 404 if not owned
    - `DELETE /sources/{source_id}` → 204; 404 if not owned
  - `tests/api/conftest.py`: `client` fixture (app + `dependency_overrides[get_session]` → `db_session`) and `auth_headers` fixture (creates a user in `db_session`, logs in, returns `{"Authorization": "Bearer …"}`).

- [ ] **Step 1: Write the shared API test fixtures**

```python
# tests/api/conftest.py
import pytest
from fastapi.testclient import TestClient
from bellwether.api.app import create_app
from bellwether.db import get_session
from bellwether.repositories.users import create_user


@pytest.fixture
def client(db_session):
    app = create_app()

    def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app)


@pytest.fixture
def auth_headers(client, db_session):
    create_user(db_session, "tester", "pw123")
    db_session.flush()
    token = client.post("/auth/token", data={"username": "tester", "password": "pw123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_watchlist_api.py
def test_requires_auth(client):
    assert client.get("/figures").status_code == 401

def test_figure_and_source_lifecycle(client, auth_headers):
    # create figure
    r = client.post("/figures", json={"name": "Powell", "type": "central_bank", "aliases": ["Jerome Powell"]},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    fig = r.json()
    assert fig["name"] == "Powell" and fig["aliases"] == ["Jerome Powell"]
    fid = fig["id"]
    # list figures
    assert [f["id"] for f in client.get("/figures", headers=auth_headers).json()] == [fid]
    # add rss source
    r = client.post(f"/figures/{fid}/sources",
                    json={"connector_type": "rss", "config": {"feed_url": "https://x/feed"}},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["connector_type"] == "rss" and src["enabled"] is True and src["origin"] == "manual"
    sid = src["id"]
    # rss without feed_url -> 422
    assert client.post(f"/figures/{fid}/sources", json={"connector_type": "rss", "config": {}},
                       headers=auth_headers).status_code == 422
    # disable the source
    r = client.patch(f"/sources/{sid}", json={"enabled": False}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["enabled"] is False
    # list sources
    assert [s["id"] for s in client.get(f"/figures/{fid}/sources", headers=auth_headers).json()] == [sid]
    # delete source + figure
    assert client.delete(f"/sources/{sid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/figures/{fid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/figures/{fid}", headers=auth_headers).status_code == 404  # already gone
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_watchlist_api.py -v`
Expected: FAIL (import error — `bellwether.api.watchlist` missing, or 404 routes).

- [ ] **Step 4: Write the schemas**

```python
# src/bellwether/api/schemas.py
from pydantic import BaseModel, ConfigDict


class FigureCreate(BaseModel):
    name: str
    type: str
    aliases: list[str] = []


class FigureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    aliases: list[str]


class SourceCreate(BaseModel):
    connector_type: str
    config: dict
    provenance: str = "primary"


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    figure_id: int
    connector_type: str
    config: dict
    provenance: str
    origin: str
    enabled: bool


class SourceUpdate(BaseModel):
    enabled: bool
```

- [ ] **Step 5: Write the watchlist router**

```python
# src/bellwether/api/watchlist.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.repositories import watchlist as repo
from bellwether.api.schemas import FigureCreate, FigureRead, SourceCreate, SourceRead, SourceUpdate

router = APIRouter()


@router.post("/figures", response_model=FigureRead, status_code=status.HTTP_201_CREATED)
def create_figure(body: FigureCreate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return repo.create_figure(session, body.name, body.type, body.aliases, owner_id=user.id)


@router.get("/figures", response_model=list[FigureRead])
def list_figures(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return repo.list_figures(session, owner_id=user.id)


@router.delete("/figures/{figure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_figure(figure_id: int, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if not repo.delete_figure(session, figure_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="Figure not found")


@router.post("/figures/{figure_id}/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def add_source(figure_id: int, body: SourceCreate, session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    if body.connector_type == "rss" and "feed_url" not in body.config:
        raise HTTPException(status_code=422, detail="rss source requires config.feed_url")
    source = repo.add_source(session, figure_id, body.connector_type, body.config,
                             body.provenance, "manual", owner_id=user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Figure not found")
    return source


@router.get("/figures/{figure_id}/sources", response_model=list[SourceRead])
def list_sources(figure_id: int, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    if repo.get_figure(session, figure_id, owner_id=user.id) is None:
        raise HTTPException(status_code=404, detail="Figure not found")
    return repo.list_sources(session, figure_id, owner_id=user.id)


@router.patch("/sources/{source_id}", response_model=SourceRead)
def update_source(source_id: int, body: SourceUpdate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    source = repo.set_source_enabled(session, source_id, body.enabled, owner_id=user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if not repo.delete_source(session, source_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="Source not found")
```

- [ ] **Step 6: Wire the router into the app**

In `src/bellwether/api/app.py`, add the import and include the router. Change the imports and `create_app` to:
```python
from bellwether.api.auth import router as auth_router
from bellwether.api.watchlist import router as watchlist_router
```
and inside `create_app`, after `app.include_router(auth_router)`, add:
```python
    app.include_router(watchlist_router)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_watchlist_api.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/api/schemas.py src/bellwether/api/watchlist.py src/bellwether/api/app.py tests/api/conftest.py tests/api/test_watchlist_api.py
git commit -m "feat: watchlist API (figures + sources) with owner scoping"
```

---

### Task 8: Statements API (list)

**Files:**
- Create: `src/bellwether/api/statements.py`, `tests/api/test_statements_api.py`
- Modify: `src/bellwether/api/app.py` (include the statements router), `src/bellwether/api/schemas.py` (add `StatementRead`)

**Interfaces:**
- Consumes: `get_session`, `get_current_user`, `list_statements` (Task 5), `create_figure`/`add_source` + `ingest_source` for the test.
- Produces:
  - `schemas.StatementRead{id,figure_id,source_id,text,url,provenance,published_at,status}` (from_attributes).
  - `statements.py` router: `GET /statements?figure_id=&status=&limit=` (auth-protected) → `list[StatementRead]`.

- [ ] **Step 1: Add the StatementRead schema**

Append to `src/bellwether/api/schemas.py`:
```python
from datetime import datetime


class StatementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    figure_id: int
    source_id: int
    text: str
    url: str | None
    provenance: str
    published_at: datetime
    status: str
```

- [ ] **Step 2: Write the failing test**

```python
# tests/api/test_statements_api.py
from pathlib import Path
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.ingest import ingest_source

FEED = str(Path(__file__).parent.parent / "fixtures" / "sample_feed.xml")

def _seed_statements(db_session, owner_id):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=owner_id)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": FEED},
               provenance="primary", origin="manual", owner_id=owner_id, enabled=True)
    db_session.add(s); db_session.flush()
    ingest_source(db_session, s)
    return f

def test_statements_requires_auth(client):
    assert client.get("/statements").status_code == 401

def test_list_statements_filters(client, auth_headers, db_session):
    # auth_headers created user "tester"; fetch its id to own the figure
    from bellwether.repositories.users import get_user_by_username
    uid = get_user_by_username(db_session, "tester").id
    f = _seed_statements(db_session, owner_id=uid)
    r = client.get(f"/statements?figure_id={f.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all("text" in s and s["provenance"] == "primary" for s in body)
    # newest first
    assert body[0]["published_at"] >= body[1]["published_at"]
    # status filter
    assert all(s["status"] == "new" for s in client.get(f"/statements?status=new", headers=auth_headers).json())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_statements_api.py -v`
Expected: FAIL (import error — `bellwether.api.statements` missing).

- [ ] **Step 4: Write the statements router**

```python
# src/bellwether/api/statements.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.repositories.statements import list_statements
from bellwether.api.schemas import StatementRead

router = APIRouter()


@router.get("/statements", response_model=list[StatementRead])
def get_statements(
    figure_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, le=500),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return list_statements(session, figure_id=figure_id, status=status, limit=limit)
```

- [ ] **Step 5: Wire the router into the app**

In `src/bellwether/api/app.py`, add:
```python
from bellwether.api.statements import router as statements_router
```
and inside `create_app`, after including the watchlist router, add:
```python
    app.include_router(statements_router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_statements_api.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (Plan 1's 16 plus Plan 2's new tests), pristine output.

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/api/statements.py src/bellwether/api/app.py src/bellwether/api/schemas.py tests/api/test_statements_api.py
git commit -m "feat: statements listing API"
```

---

## Self-Review

**Spec coverage (Plan 2's slice):** user-managed watchlist (figures/sources CRUD, owner-scoped) — Tasks 4, 7 ✓; connector interface + RSS connector (X/YouTube/news deferred, behind the same interface) — Tasks 2, 3, 6 ✓; normalized `statements` with precise `published_at`, provenance, URL, dedup — Tasks 1, 6 ✓; manual source add — Task 7 ✓; statements are shared corpus (no `owner_id`) — Task 1 ✓; read-only ingestion — Tasks 3, 6 ✓; statements query — Tasks 5, 8 ✓. Deferred with intent: the generic `FOR UPDATE SKIP LOCKED` worker/queue (→ Plan 3, first status-consumer), scheduling/polling loop as a long-running process (→ Plan 3+), X/YouTube/news connectors and auto-discovery (→ Plan 6).

**Placeholder scan:** every code step shows complete code; every command has expected output. No TBD/TODO.

**Type consistency:** `Figure`/`Source`/`Statement` columns are used identically across Tasks 1/4/5/6/7/8; repository signatures (`create_figure`, `add_source`, `list_statements`, …) match their call sites in the API and ingestor; `build_connector`/`UnknownConnectorType` defined in Task 6 and consumed in the same task's ingestor; schema field names match model attributes for `from_attributes`.

**Note for the implementer of Task 8:** the API exposes `text`, not the raw `external_id` dedup key — do not add an `external_id` field to `StatementRead`.
