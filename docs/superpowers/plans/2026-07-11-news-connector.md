# News Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `news` connector that fetches recent news about a figure via Google News's free search-RSS feed, auto-created (enabled) when a figure is added and polled by the existing ingest scheduler.

**Architecture:** `NewsConnector(query)` builds a Google News search-RSS URL and delegates fetch+parse to the existing `RssConnector` (inheriting its SSL/dedup/dating). It's registered under `connector_type="news"`. The `POST /figures` handler auto-creates one enabled `news` source per figure; a backfill helper covers pre-existing figures. No ingest-scheduler change.

**Tech Stack:** Python, SQLAlchemy, FastAPI, feedparser (via RssConnector), pytest (real Postgres).

## Global Constraints

- **Provider = Google News search RSS**, no key: `https://news.google.com/rss/search?q=<quoted query> when:<N>d&hl=en-US&gl=US&ceid=US:en`.
- **`NewsConnector` delegates to `RssConnector`** — do NOT reimplement feed fetch/parse.
- **Register `"news"`**: in `build_connector` AND add to `KNOWN_CONNECTOR_TYPES` (`frozenset({"rss", "x", "news"})`).
- **Two settings** in `config.py` (plain `Settings` int fields; Google News needs no credentials): `news_recency_days: int = 7`, `news_poll_interval_seconds: int = 1800`.
- **Auto-create on figure add** in the `POST /figures` handler (not in `repo.create_figure`): one `news` source, `config={"query": figure.name}`, `enabled=True`, `status="active"`, `origin="auto"`, `provenance="news"`, `poll_interval_seconds=<news_poll_interval_seconds>`. Deterministic → enabled immediately, no discovery/review gate.
- **Backfill** helper creates a `news` source for any figure lacking one; never duplicates.
- Real-Postgres tests via `db_session` / api `client` fixtures; seed with `owner_id=None`. Do not modify `tests/conftest.py` or `tests/api/conftest.py`.
- No change to the ingest scheduler, detect/extract/resolve/measure, or the discovery pipeline beyond adding `"news"` to `KNOWN_CONNECTOR_TYPES`.
- Live verification before merge (external feeds stubbed in the suite).

---

### Task 1: NewsConnector + registry + settings

**Files:**
- Create: `src/bellwether/connectors/news.py`
- Modify: `src/bellwether/connectors/registry.py` (import + `KNOWN_CONNECTOR_TYPES` + `build_connector` branch)
- Modify: `src/bellwether/config.py` (two settings)
- Test: `tests/test_news_connector.py` (create)

**Interfaces:**
- Consumes: `bellwether.connectors.rss.RssConnector` (existing — `RssConnector(feed_url).fetch() -> list[RawItem]`); `bellwether.config.get_settings()`.
- Produces: `NewsConnector(query: str, recency_days: int = 7)` with attribute `.url` and `.fetch() -> list[RawItem]`; `build_connector` returns a `NewsConnector` for `connector_type="news"`; `KNOWN_CONNECTOR_TYPES` includes `"news"`; `Settings.news_recency_days`, `Settings.news_poll_interval_seconds`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_news_connector.py`:

```python
import urllib.parse
from bellwether.connectors.news import NewsConnector
from bellwether.connectors.registry import build_connector, KNOWN_CONNECTOR_TYPES
from bellwether.models.source import Source
from bellwether.config import Settings


def test_news_url_construction():
    conn = NewsConnector("Jerome Powell", recency_days=7)
    assert conn.url == (
        "https://news.google.com/rss/search?"
        "q=%22Jerome%20Powell%22%20when%3A7d&hl=en-US&gl=US&ceid=US:en"
    )


def test_news_fetch_delegates_to_rss(monkeypatch):
    captured = {}

    class StubRss:
        def __init__(self, url):
            captured["url"] = url

        def fetch(self):
            return ["a", "b"]

    monkeypatch.setattr("bellwether.connectors.news.RssConnector", StubRss)
    conn = NewsConnector("Powell", recency_days=7)
    assert conn.fetch() == ["a", "b"]
    assert captured["url"] == conn.url


def test_build_connector_returns_news_connector():
    src = Source(connector_type="news", config={"query": "Powell"})
    conn = build_connector(src)
    assert isinstance(conn, NewsConnector)
    assert "Powell" in urllib.parse.unquote(conn.url)


def test_news_in_known_connector_types():
    assert "news" in KNOWN_CONNECTOR_TYPES


def test_news_settings_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.news_recency_days == 7
    assert s.news_poll_interval_seconds == 1800
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_news_connector.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bellwether.connectors.news'`

- [ ] **Step 3: Create the `NewsConnector`**

Create `src/bellwether/connectors/news.py`:

```python
import urllib.parse
from bellwether.connectors.base import RawItem
from bellwether.connectors.rss import RssConnector


class NewsConnector:
    """Fetch recent news about `query` via Google News's search-RSS feed.

    Delegates the actual fetch + parse to RssConnector, which handles SSL_CONTEXT,
    dedup by external_id, and published-date parsing.
    """

    def __init__(self, query: str, recency_days: int = 7):
        q = urllib.parse.quote(f'"{query}" when:{recency_days}d')
        self.url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

    def fetch(self) -> list[RawItem]:
        return RssConnector(self.url).fetch()
```

- [ ] **Step 4: Add the two settings**

In `src/bellwether/config.py`, inside the `Settings` class (e.g. immediately after the
`discovery_confidence_threshold` field on line 28), add:

```python
    news_recency_days: int = 7
    news_poll_interval_seconds: int = 1800
```

- [ ] **Step 5: Wire `news` into the registry**

In `src/bellwether/connectors/registry.py`:

Change the imports at the top from:

```python
import os
from bellwether.connectors.base import SourceConnector
from bellwether.connectors.rss import RssConnector
from bellwether.connectors.x import XConnector
from bellwether.models.source import Source
```

to (add `NewsConnector` and `get_settings`):

```python
import os
from bellwether.connectors.base import SourceConnector
from bellwether.connectors.rss import RssConnector
from bellwether.connectors.x import XConnector
from bellwether.connectors.news import NewsConnector
from bellwether.config import get_settings
from bellwether.models.source import Source
```

Change `KNOWN_CONNECTOR_TYPES` from:

```python
KNOWN_CONNECTOR_TYPES = frozenset({"rss", "x"})
```

to:

```python
KNOWN_CONNECTOR_TYPES = frozenset({"rss", "x", "news"})
```

And add a `news` branch in `build_connector`, before the final `raise`:

```python
    if source.connector_type == "news":
        return NewsConnector(source.config["query"], recency_days=get_settings().news_recency_days)
    raise UnknownConnectorType(source.connector_type)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_news_connector.py -q`
Expected: PASS (5 passed)

- [ ] **Step 7: Run the connector/registry/ingest suites for regressions**

Run: `.venv/bin/python -m pytest tests/test_news_connector.py tests/test_ingest.py tests/test_rss_connector.py tests/discovery/test_pipeline.py -q`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/connectors/news.py src/bellwether/connectors/registry.py src/bellwether/config.py tests/test_news_connector.py
git commit -m "feat: news connector (Google News RSS) + registry + settings"
```

---

### Task 2: Auto-create news source on figure add + backfill

**Files:**
- Modify: `src/bellwether/repositories/watchlist.py` (add `create_news_source` + `backfill_news_sources`)
- Modify: `src/bellwether/api/watchlist.py` (call `create_news_source` in the `POST /figures` handler)
- Test: `tests/test_news_backfill.py` (create — repo helpers), `tests/api/test_news_autocreate.py` (create — API path)

**Interfaces:**
- Consumes: `NewsConnector`/`"news"` registration (Task 1); `bellwether.config.get_settings()`; existing `repo.create_figure`.
- Produces: `repo.create_news_source(session, figure, owner_id) -> Source`; `repo.backfill_news_sources(session) -> int`; `POST /figures` now also creates a `news` source.

- [ ] **Step 1: Write the failing repo tests**

Create `tests/test_news_backfill.py`:

```python
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.repositories import watchlist as repo


def _figure(db_session, name="Powell"):
    f = Figure(name=name, type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    return f


def test_create_news_source(db_session):
    f = _figure(db_session, "Jerome Powell")
    src = repo.create_news_source(db_session, f, owner_id=None)
    assert src.connector_type == "news"
    assert src.config["query"] == "Jerome Powell"
    assert src.enabled is True
    assert src.status == "active"
    assert src.origin == "auto"
    assert src.provenance == "news"
    assert src.poll_interval_seconds == 1800


def test_backfill_creates_missing_and_skips_existing(db_session):
    f1 = _figure(db_session, "A")                         # no news source
    f2 = _figure(db_session, "B")
    repo.create_news_source(db_session, f2, owner_id=None)  # already has one
    created = repo.backfill_news_sources(db_session)
    assert created == 1                                   # only f1 got one
    for fid in (f1.id, f2.id):
        news = db_session.execute(
            select(Source).where(Source.figure_id == fid, Source.connector_type == "news")
        ).scalars().all()
        assert len(news) == 1                             # exactly one each, no dup
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_news_backfill.py -q`
Expected: FAIL — `AttributeError: module 'bellwether.repositories.watchlist' has no attribute 'create_news_source'`

- [ ] **Step 3: Add the repo helpers**

In `src/bellwether/repositories/watchlist.py`, add this import near the top (with the other imports):

```python
from bellwether.config import get_settings
```

and add these two functions (e.g. after `add_source`):

```python
def create_news_source(session: Session, figure: Figure, owner_id: int | None) -> Source:
    """Auto-create an enabled Google-News source for a figure (query = the figure's name).

    Deterministic, so it is enabled immediately — it does not pass through the discovery
    confidence gate that LLM-proposed sources use.
    """
    source = Source(
        figure_id=figure.id, connector_type="news", config={"query": figure.name},
        provenance="news", origin="auto", owner_id=owner_id,
        enabled=True, status="active",
        poll_interval_seconds=get_settings().news_poll_interval_seconds,
    )
    session.add(source)
    session.flush()
    return source


def backfill_news_sources(session: Session) -> int:
    """Create a news source for every figure that lacks one. Returns the count created."""
    created = 0
    for figure in session.execute(select(Figure)).scalars().all():
        has_news = session.execute(
            select(Source).where(Source.figure_id == figure.id, Source.connector_type == "news")
        ).first()
        if has_news is None:
            create_news_source(session, figure, owner_id=figure.owner_id)
            created += 1
    session.flush()
    return created
```

- [ ] **Step 4: Run the repo tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_news_backfill.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Write the failing API auto-create test**

Create `tests/api/test_news_autocreate.py`:

```python
from sqlalchemy import select
from bellwether.models.source import Source


def test_create_figure_auto_creates_enabled_news_source(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Jerome Powell", "type": "individual"},
                    headers=auth_headers)
    assert r.status_code == 201
    fid = r.json()["id"]
    news = db_session.execute(
        select(Source).where(Source.figure_id == fid, Source.connector_type == "news")
    ).scalars().all()
    assert len(news) == 1
    n = news[0]
    assert n.config["query"] == "Jerome Powell"
    assert n.enabled is True
    assert n.provenance == "news"
    assert n.origin == "auto"
    assert n.poll_interval_seconds == 1800
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_news_autocreate.py -q`
Expected: FAIL — `assert len(news) == 1` fails with `0` (the endpoint does not yet create the source).

- [ ] **Step 7: Wire the auto-create into the `POST /figures` handler**

In `src/bellwether/api/watchlist.py`, change the `create_figure` handler from:

```python
@router.post("/figures", response_model=FigureRead, status_code=status.HTTP_201_CREATED)
def create_figure(body: FigureCreate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return repo.create_figure(session, body.name, body.type, body.aliases, owner_id=user.id,
                              discover=body.discover)
```

to:

```python
@router.post("/figures", response_model=FigureRead, status_code=status.HTTP_201_CREATED)
def create_figure(body: FigureCreate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    figure = repo.create_figure(session, body.name, body.type, body.aliases, owner_id=user.id,
                                discover=body.discover)
    repo.create_news_source(session, figure, owner_id=user.id)
    return figure
```

- [ ] **Step 8: Run the API test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_news_autocreate.py -q`
Expected: PASS (1 passed)

- [ ] **Step 9: Run the full suite; fix any test that counted sources on an API-created figure**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass EXCEPT the known pre-existing `tests/test_config_alerts.py::test_alert_defaults` (environmental `.env` CORS failure — leave it). If any OTHER test now fails because a figure created via `POST /figures` gained a `news` source (e.g. it asserted an exact source count), update that test's expectation to include the auto-created news source — this is the intended new behavior. Report any such test you changed.

- [ ] **Step 10: Commit**

```bash
git add src/bellwether/repositories/watchlist.py src/bellwether/api/watchlist.py tests/test_news_backfill.py tests/api/test_news_autocreate.py
git commit -m "feat: auto-create news source on figure add + backfill helper"
```

---

## Live verification (before merge, not a task)

With Postgres up and the API running (or via a script), confirm real news flows and backfill works:

```bash
# 1. backfill existing figures (e.g. the current Trump figure), then confirm it got a news source
.venv/bin/python -c "from bellwether.db import SessionLocal; from bellwether.repositories.watchlist import backfill_news_sources; s=SessionLocal(); print('created', backfill_news_sources(s)); s.commit(); s.close()"

# 2. poll the news source for a real figure and confirm dated articles become statements:
export LITELLM_LOCAL_MODEL_COST_MAP=True
.venv/bin/python -c "
from bellwether.db import SessionLocal
from sqlalchemy import select
from bellwether.models.source import Source
from bellwether.ingest import ingest_source
s=SessionLocal()
src=s.execute(select(Source).where(Source.connector_type=='news')).scalars().first()
new=ingest_source(s, src)
print('news poll created', len(new), 'statements; sample:', [x.text.splitlines()[0][:60] for x in new[:3]])
s.rollback(); s.close()
"
```

Expected: backfill reports the count; the news poll returns a non-zero number of real dated
headlines as `new` statements. (Roll back so the live probe doesn't flood the pipeline.)

## Self-review notes

- **Spec coverage:** NewsConnector + delegation (Task 1) ✓; registry + KNOWN_CONNECTOR_TYPES (Task 1 Step 5) ✓; two settings (Task 1 Step 4) ✓; auto-create on figure add with the exact field values (Task 2 Steps 3+7) ✓; backfill no-dup (Task 2 Step 1/3) ✓; `provenance="news"` ✓; recency `when:Nd` via setting (Task 1) ✓; live verification (dedicated section) ✓; ingest scheduler unchanged ✓.
- **Type consistency:** `NewsConnector(query, recency_days=7)` / `.url` / `.fetch()` defined Task 1, used in registry + tests; `create_news_source(session, figure, owner_id) -> Source` and `backfill_news_sources(session) -> int` defined Task 2 Step 3, used by the API handler + tests.
- **Placeholder scan:** none — every step carries concrete code/commands.
- Field values (`origin="auto"`, `provenance="news"`, `status="active"`, `enabled=True`, `poll_interval_seconds=1800`) are identical between Task 2's helper, its tests, and the API test.
