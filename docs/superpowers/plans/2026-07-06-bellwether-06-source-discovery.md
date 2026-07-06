# bellwether Plan 6 — Source Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adding a figure by name auto-discovers its verified sources — a `discovery` worker stage that runs a deterministic Wikidata backbone + a DSPy Discovery module (disambiguation + Tavily gap-fill), cross-references independent authorities into a confidence gate (auto-enable vs `pending_review`), plus a real X ingestion connector.

**Architecture:** All network/LLM clients sit behind protocols + `build_*()` factories (the market/LLM seam pattern), stubbed in the suite and live-verified. Orchestration lives in a `discovery/` package (`run_discovery` over injected clients); `worker.py` stays Wikidata/Tavily/DSPy-free. No new tables — discovery rides on extended `figures` + `sources`, generalizing the SKIP-LOCKED queue harness to claim figures.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, Alembic, Postgres, FastAPI, DSPy 3.2.1 (`Predict`, `DummyLM`), `feedparser` (already a dep), stdlib `urllib`/`html.parser`, pytest. Design spec: `docs/superpowers/specs/2026-07-06-bellwether-06-source-discovery-design.md`.

## Global Constraints

- Python **3.11+**; SQLAlchemy 2.0 (`Mapped`/`mapped_column`); JSONB via `sqlalchemy.dialects.postgresql`.
- **No new tables.** Extend `figures` (`wikidata_id`, `discovery_status`, `discovery_claimed_at`, `discovery_error`) and `sources` (`status`, `verified`, `discovery_confidence`, `discovery_meta`).
- **The LLM proposes, deterministic verification disposes.** Only cross-referenced authorities clear the confidence gate; an LLM/Tavily candidate can never auto-enable itself.
- **Confidence model (deterministic):** signal weights `wikidata=0.6, domain_match=0.3, x_verified=0.2, reachable=0.2`; `discovery_confidence` = clamped sum; gate = `>= discovery_confidence_threshold` (default **0.7**) → `verified=true, status=active, enabled=true`; else `status=pending_review, enabled=false`.
- **Keyless by default.** Wikidata is keyless; Tavily needs `TAVILY_API_KEY` (no key → gap-fill skipped); X verify + X connector need `X_API_KEY` (no key → verifier `None`, X connector ships `enabled=false`). Credentials are plain env vars, **never `Settings` fields**.
- **`worker.py` imports no Wikidata/Tavily/DSPy code.** The discovery stage calls `discovery/pipeline.py`; transient external failures raise `DiscoveryError` (retryable), "no match" is terminal.
- **Owner-scoping:** review queue + submit scoped by figure ownership. `figures`/`sources` keep nullable `owner_id`.
- **Idempotent re-runs:** discovery upserts sources by natural key (`figure_id` + `connector_type` + config identity); never touches `origin="manual"`.
- **Tests: real Postgres, no live network.** All external clients + the Discovery module stubbed behind seams; pure logic unit-tested; live Wikidata/Tavily/X verified manually. Use `.venv/bin/python -m pytest …` / `.venv/bin/alembic …`. Postgres via `docker compose up -d`. A yfinance transitive import adds ~15-30s to a full-suite run — normal. If a full-suite run **hangs/times out**, report it — do NOT restart Postgres from a subagent.

## File Structure

```
src/bellwether/
├── models/figure.py, source.py            # (modify) discovery columns
├── discovery/
│   ├── __init__.py
│   ├── contracts.py    # DTOs + Protocols (Wikidata/WebSearch/XVerifier/Discoverer/HttpClient); SourceBinding; DiscoveryError
│   ├── http.py         # HttpClient adapter (urllib) + build_http()
│   ├── verify.py       # score_binding(signals, threshold) -> (confidence, verified)  [pure]
│   ├── connectors.py   # youtube_feed_url, x_binding, domain_of, discover_feed_links  [pure]
│   ├── wikidata.py     # parse fns (pure) + WikidataAdapter + build_wikidata()
│   ├── websearch.py    # Tavily WebSearch adapter + build_web_search()
│   ├── xverify.py      # XVerifier adapter + build_x_verifier()
│   ├── discoverer.py   # DSPy Discovery module + build_discoverer()
│   └── pipeline.py     # run_discovery(session, figure, *, clients) -> None (writes sources)
├── connectors/x.py     # (new) X ingestion connector; registry maps "x"
├── connectors/registry.py  # (modify) add "x"
├── queue.py            # (modify) claim_pending_figure / reclaim_stale_figures
├── worker.py           # (modify) make_discovery_stage + CLI "discovery"; stays clean
├── api/watchlist.py    # (modify) POST /figures discover flag; POST /figures/{id}/discover
├── api/discovery.py    # (new) GET /discovery/queue, POST /discovery/{source_id}
├── api/schemas.py      # (modify) discovery fields + queue/decision schemas
├── api/app.py          # (modify) include discovery router
├── repositories/figures.py (or watchlist repo)  # (modify) create_figure discovery_status
└── config.py           # (modify) discovery_model, discovery_confidence_threshold
migrations/versions/    # ONE migration
```

---

### Task 1: Models + migration (discovery columns)

**Files:**
- Modify: `src/bellwether/models/figure.py`, `src/bellwether/models/source.py`
- Create: the generated migration
- Test: `tests/test_models_discovery.py`

**Interfaces:**
- Produces: `Figure` gains `wikidata_id: str|None` (indexed), `discovery_status: str` (default `"pending"`), `discovery_claimed_at: datetime|None`, `discovery_error: str|None`. `Source` gains `status: str` (default `"active"`), `verified: bool` (default `False`), `discovery_confidence: float|None`, `discovery_meta: dict|None` (JSONB).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_discovery.py
from bellwether.models.figure import Figure
from bellwether.models.source import Source


def test_figure_discovery_columns():
    c = set(Figure.__table__.columns.keys())
    assert {"wikidata_id", "discovery_status", "discovery_claimed_at", "discovery_error"} <= c


def test_source_discovery_columns():
    c = set(Source.__table__.columns.keys())
    assert {"status", "verified", "discovery_confidence", "discovery_meta"} <= c
    assert Source.__table__.columns["status"].default.arg == "active"
    assert Source.__table__.columns["verified"].default.arg is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models_discovery.py -v`
Expected: FAIL (`AttributeError`/`KeyError` — columns missing).

- [ ] **Step 3: Add the Figure columns**

In `src/bellwether/models/figure.py`, add imports `Boolean, Float, Text` as needed and these columns after `aliases`:
```python
    wikidata_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    discovery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    discovery_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```
(Add `Text` to the `sqlalchemy` import line; `DateTime`/`String` are already imported.)

- [ ] **Step 4: Add the Source columns**

In `src/bellwether/models/source.py`, add the columns after `last_polled_at` (import `Float`, `Text` as needed; `JSONB` already imported):
```python
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    discovery_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    discovery_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 5: Run the model test**

Run: `.venv/bin/python -m pytest tests/test_models_discovery.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Generate + apply the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "add discovery columns to figures and sources"
.venv/bin/alembic upgrade head
```
Open the generated file; confirm it `add_column`s the four figure columns (with the `ix_figures_wikidata_id` index) and the four source columns, and `server_default`/`default` is handled (autogenerate emits `add_column` with `nullable=False` + a default for `discovery_status`/`status`/`verified` — if the batch fails on existing rows, add `server_default` in the migration: `discovery_status server_default='pending'`, `status server_default='active'`, `verified server_default='false'`). Verify `alembic heads` shows one head.

- [ ] **Step 7: Full suite + commit**

Run: `.venv/bin/python -m pytest -q` → all green.
```bash
git add src/bellwether/models tests/test_models_discovery.py migrations
git commit -m "feat: discovery columns on figures + sources + migration"
```

---

### Task 2: Config (discovery settings)

**Files:**
- Modify: `src/bellwether/config.py`, `.env.example`
- Test: `tests/test_config_discovery.py`

**Interfaces:**
- Produces on `Settings`: `discovery_model: str = "anthropic/claude-sonnet-5"`, `discovery_confidence_threshold: float = 0.7`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_discovery.py
from bellwether.config import Settings


def test_discovery_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.discovery_model == "anthropic/claude-sonnet-5"
    assert s.discovery_confidence_threshold == 0.7
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_config_discovery.py -v` — FAIL (`AttributeError`).

- [ ] **Step 3: Add settings**

In `src/bellwether/config.py`, after the Plan-5 settings, add:
```python
    discovery_model: str = "anthropic/claude-sonnet-5"
    discovery_confidence_threshold: float = 0.7
```

- [ ] **Step 4: Document `.env.example`**

Append:
```bash
# --- Source discovery (Plan 6) ---
# DISCOVERY_MODEL=anthropic/claude-sonnet-5   # disambiguation / gap-fill LLM
# DISCOVERY_CONFIDENCE_THRESHOLD=0.7          # >= this auto-enables a binding; below -> pending_review
# TAVILY_API_KEY=...   # web-search gap-fill; no key -> gap-fill skipped, Wikidata backbone still runs
# X_API_KEY=...        # X verify + X ingestion connector; no key -> X verify no-ops, X connector disabled
```

- [ ] **Step 5: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/test_config_discovery.py -v` — PASS.
```bash
git add src/bellwether/config.py .env.example tests/test_config_discovery.py
git commit -m "feat: discovery config (model + confidence threshold)"
```

---

### Task 3: Contracts + DTOs + HTTP seam

**Files:**
- Create: `src/bellwether/discovery/__init__.py` (empty), `src/bellwether/discovery/contracts.py`, `src/bellwether/discovery/http.py`
- Test: `tests/discovery/__init__.py` (empty), `tests/discovery/test_contracts.py`

**Interfaces:**
- Produces (in `contracts.py`): dataclasses `WikidataEntity(qid,label,description)`, `WikidataClaims(website,x_username,youtube_channel,aliases)`, `SearchResult(title,url,snippet)`, `XStatus(exists,verified)`, `Disambiguation(qid,confidence)`, `SourceCandidate(connector_type,config,rationale)`, `FetchResult(ok,text)`, `SourceBinding(connector_type,config,origin,status,verified,discovery_confidence,discovery_meta,enabled)`; Protocols `WikidataClient` (`search(name)->list[WikidataEntity]`, `claims(qid)->WikidataClaims`), `WebSearch` (`search(query)->list[SearchResult]`), `XVerifier` (`verify(handle)->XStatus|None`), `Discoverer` (`disambiguate(name,candidates)->Disambiguation`, `gapfill(figure_name,known,results)->list[SourceCandidate]`), `HttpClient` (`get(url)->FetchResult`); exception `DiscoveryError`.
- Produces (in `http.py`): `UrllibHttpClient` + `build_http() -> HttpClient`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_contracts.py
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, SearchResult, XStatus, Disambiguation,
    SourceCandidate, FetchResult, SourceBinding, DiscoveryError,
    WikidataClient, WebSearch, XVerifier, Discoverer, HttpClient,
)


def test_dtos_hold_fields():
    e = WikidataEntity(qid="Q1", label="X", description="d")
    assert e.qid == "Q1"
    b = SourceBinding(connector_type="rss", config={"feed_url": "u"}, origin="discovered",
                      status="active", verified=True, discovery_confidence=0.9,
                      discovery_meta={"wikidata": True}, enabled=True)
    assert b.status == "active" and b.enabled is True
    assert issubclass(DiscoveryError, Exception)


def test_stub_satisfies_protocols():
    class W:
        def search(self, name): return []
        def claims(self, qid): return WikidataClaims(None, None, None, [])
    class H:
        def get(self, url): return FetchResult(ok=True, text="<html></html>")
    assert isinstance(W(), WikidataClient)
    assert isinstance(H(), HttpClient)
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_contracts.py -v` — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `contracts.py`**

Create empty `src/bellwether/discovery/__init__.py` and `tests/discovery/__init__.py`, then:
```python
# src/bellwether/discovery/contracts.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WikidataEntity:
    qid: str
    label: str
    description: str


@dataclass(frozen=True)
class WikidataClaims:
    website: str | None
    x_username: str | None
    youtube_channel: str | None
    aliases: list[str]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class XStatus:
    exists: bool
    verified: bool


@dataclass(frozen=True)
class Disambiguation:
    qid: str | None
    confidence: float


@dataclass(frozen=True)
class SourceCandidate:
    connector_type: str
    config: dict
    rationale: str


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    text: str | None


@dataclass
class SourceBinding:
    connector_type: str
    config: dict
    origin: str
    status: str
    verified: bool
    discovery_confidence: float
    discovery_meta: dict
    enabled: bool


class DiscoveryError(Exception):
    """Transient external failure — the discovery run is retryable."""


@runtime_checkable
class WikidataClient(Protocol):
    def search(self, name: str) -> list[WikidataEntity]: ...
    def claims(self, qid: str) -> WikidataClaims: ...


@runtime_checkable
class WebSearch(Protocol):
    def search(self, query: str) -> list[SearchResult]: ...


@runtime_checkable
class XVerifier(Protocol):
    def verify(self, handle: str) -> XStatus | None: ...


@runtime_checkable
class Discoverer(Protocol):
    def disambiguate(self, name: str, candidates: list[WikidataEntity]) -> Disambiguation: ...
    def gapfill(self, figure_name: str, known: list[str], results: list[SearchResult]) -> list[SourceCandidate]: ...


@runtime_checkable
class HttpClient(Protocol):
    def get(self, url: str) -> FetchResult: ...
```

- [ ] **Step 4: Write `http.py`**

```python
# src/bellwether/discovery/http.py
import urllib.request
from bellwether.discovery.contracts import FetchResult, HttpClient


class UrllibHttpClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def get(self, url: str) -> FetchResult:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bellwether/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return FetchResult(ok=False, text=None)
                raw = resp.read()
                return FetchResult(ok=True, text=raw.decode("utf-8", errors="replace"))
        except Exception:
            return FetchResult(ok=False, text=None)


def build_http() -> HttpClient:
    return UrllibHttpClient()
```

- [ ] **Step 5: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_contracts.py -v` — PASS (2 tests).
```bash
git add src/bellwether/discovery tests/discovery
git commit -m "feat: discovery contracts/DTOs + HTTP fetch seam"
```

---

### Task 4: Verify (pure confidence model)

**Files:**
- Create: `src/bellwether/discovery/verify.py`
- Test: `tests/discovery/test_verify.py`

**Interfaces:**
- Produces: `SIGNAL_WEIGHTS` dict; `score_binding(signals: dict, threshold: float) -> tuple[float, bool]` — `confidence = min(1.0, sum(weight for signal truthy))`, `verified = confidence >= threshold`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_verify.py
from bellwether.discovery.verify import score_binding


def test_single_signal_below_bar():
    conf, verified = score_binding({"wikidata": True}, 0.7)
    assert conf == 0.6 and verified is False


def test_two_signals_clear_bar():
    conf, verified = score_binding({"wikidata": True, "domain_match": True}, 0.7)
    assert abs(conf - 0.9) < 1e-9 and verified is True


def test_llm_proposal_weak_stays_pending():
    # no wikidata: domain_match + reachable = 0.5 -> pending
    conf, verified = score_binding({"domain_match": True, "reachable": True}, 0.7)
    assert abs(conf - 0.5) < 1e-9 and verified is False


def test_llm_proposal_strong_x_clears():
    conf, verified = score_binding({"domain_match": True, "x_verified": True, "reachable": True}, 0.7)
    assert abs(conf - 0.7) < 1e-9 and verified is True


def test_falsy_and_unknown_signals_ignored():
    conf, verified = score_binding({"wikidata": True, "x_verified": None, "bogus": True}, 0.7)
    assert conf == 0.6 and verified is False
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_verify.py -v` — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `verify.py`**

```python
# src/bellwether/discovery/verify.py
SIGNAL_WEIGHTS = {"wikidata": 0.6, "domain_match": 0.3, "x_verified": 0.2, "reachable": 0.2}


def score_binding(signals: dict, threshold: float) -> tuple[float, bool]:
    confidence = min(1.0, sum(w for k, w in SIGNAL_WEIGHTS.items() if signals.get(k)))
    return confidence, confidence >= threshold
```

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_verify.py -v` — PASS (5 tests).
```bash
git add src/bellwether/discovery/verify.py tests/discovery/test_verify.py
git commit -m "feat: deterministic discovery confidence model (pure)"
```

---

### Task 5: Connector mapping (pure)

**Files:**
- Create: `src/bellwether/discovery/connectors.py`
- Test: `tests/discovery/test_connector_mapping.py`

**Interfaces:**
- Produces: `youtube_feed_url(channel_id) -> str`; `x_binding(handle) -> tuple[str, dict]` (`("x", {"handle": ...})`); `domain_of(url) -> str` (netloc, `www.` stripped, lowercased); `discover_feed_links(html: str) -> list[str]` (hrefs of `<link rel="alternate" type="application/rss+xml|atom+xml">`).

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_connector_mapping.py
from bellwether.discovery.connectors import youtube_feed_url, x_binding, domain_of, discover_feed_links


def test_youtube_feed_url():
    assert youtube_feed_url("UC123") == "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"


def test_x_binding():
    assert x_binding("@jack") == ("x", {"handle": "jack"})
    assert x_binding("jack") == ("x", {"handle": "jack"})


def test_domain_of():
    assert domain_of("https://www.Example.com/path") == "example.com"
    assert domain_of("example.com") == "example.com"


def test_discover_feed_links():
    html = '''<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="https://x.com/atom">
      <link rel="stylesheet" href="/s.css">
    </head></html>'''
    assert discover_feed_links(html) == ["/feed.xml", "https://x.com/atom"]
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_connector_mapping.py -v` — FAIL.

- [ ] **Step 3: Write `connectors.py`**

```python
# src/bellwether/discovery/connectors.py
from html.parser import HTMLParser
from urllib.parse import urlparse


def youtube_feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def x_binding(handle: str) -> tuple[str, dict]:
    return "x", {"handle": handle.lstrip("@")}


def domain_of(url: str) -> str:
    netloc = urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


class _FeedLinkParser(HTMLParser):
    _FEED_TYPES = ("application/rss+xml", "application/atom+xml")

    def __init__(self):
        super().__init__()
        self.feeds: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: v for k, v in attrs}
        if a.get("rel") == "alternate" and a.get("type") in self._FEED_TYPES and a.get("href"):
            self.feeds.append(a["href"])


def discover_feed_links(html: str) -> list[str]:
    parser = _FeedLinkParser()
    parser.feed(html)
    return parser.feeds
```

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_connector_mapping.py -v` — PASS (4 tests).
```bash
git add src/bellwether/discovery/connectors.py tests/discovery/test_connector_mapping.py
git commit -m "feat: discovery connector mapping (youtube/x/domain/feed-links)"
```

---

### Task 6: Wikidata client

**Files:**
- Create: `src/bellwether/discovery/wikidata.py`
- Test: `tests/discovery/test_wikidata.py`

**Interfaces:**
- Consumes: `WikidataEntity`, `WikidataClaims`, `WikidataClient`, `DiscoveryError` (Task 3).
- Produces: pure parsers `_parse_search(payload: dict) -> list[WikidataEntity]` and `_parse_claims(payload: dict) -> WikidataClaims`; `WikidataAdapter` (calls `wbsearchentities` + `wbgetentities` via an injected `HttpClient`, raising `DiscoveryError` on failure); `build_wikidata() -> WikidataClient`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_wikidata.py
import pytest
from bellwether.discovery.wikidata import _parse_search, _parse_claims, WikidataAdapter
from bellwether.discovery.contracts import FetchResult, DiscoveryError


def test_parse_search():
    payload = {"search": [
        {"id": "Q13129", "label": "Jerome Powell", "description": "American attorney"},
        {"id": "Q42", "label": "Other", "description": ""},
    ]}
    ents = _parse_search(payload)
    assert [e.qid for e in ents] == ["Q13129", "Q42"]
    assert ents[0].label == "Jerome Powell"


def test_parse_claims():
    payload = {"entities": {"Q1": {
        "claims": {
            "P856": [{"mainsnak": {"datavalue": {"value": "https://federalreserve.gov"}}}],
            "P2002": [{"mainsnak": {"datavalue": {"value": "federalreserve"}}}],
            "P2397": [{"mainsnak": {"datavalue": {"value": "UCabc"}}}],
        },
        "aliases": {"en": [{"value": "Jay Powell"}]},
    }}}
    claims = _parse_claims(payload)
    assert claims.website == "https://federalreserve.gov"
    assert claims.x_username == "federalreserve"
    assert claims.youtube_channel == "UCabc"
    assert claims.aliases == ["Jay Powell"]


def test_adapter_raises_discovery_error_on_fetch_failure():
    class BadHttp:
        def get(self, url): return FetchResult(ok=False, text=None)
    with pytest.raises(DiscoveryError):
        WikidataAdapter(BadHttp()).search("anything")
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_wikidata.py -v` — FAIL.

- [ ] **Step 3: Write `wikidata.py`**

```python
# src/bellwether/discovery/wikidata.py
import json
from urllib.parse import urlencode
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, WikidataClient, HttpClient, DiscoveryError,
)
from bellwether.discovery.http import build_http

_API = "https://www.wikidata.org/w/api.php"


def _parse_search(payload: dict) -> list[WikidataEntity]:
    return [
        WikidataEntity(qid=r["id"], label=r.get("label", ""), description=r.get("description", ""))
        for r in payload.get("search", [])
    ]


def _first(claims: dict, prop: str) -> str | None:
    entries = claims.get(prop)
    if not entries:
        return None
    try:
        return entries[0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_claims(payload: dict) -> WikidataClaims:
    entity = next(iter(payload.get("entities", {}).values()), {})
    claims = entity.get("claims", {})
    aliases = [a["value"] for a in entity.get("aliases", {}).get("en", [])]
    return WikidataClaims(
        website=_first(claims, "P856"),
        x_username=_first(claims, "P2002"),
        youtube_channel=_first(claims, "P2397"),
        aliases=aliases,
    )


class WikidataAdapter:
    def __init__(self, http: HttpClient):
        self._http = http

    def _get_json(self, params: dict) -> dict:
        res = self._http.get(f"{_API}?{urlencode(params)}")
        if not res.ok or res.text is None:
            raise DiscoveryError("wikidata request failed")
        try:
            return json.loads(res.text)
        except json.JSONDecodeError as exc:
            raise DiscoveryError("wikidata returned invalid JSON") from exc

    def search(self, name: str) -> list[WikidataEntity]:
        return _parse_search(self._get_json({
            "action": "wbsearchentities", "search": name, "language": "en",
            "format": "json", "limit": "5",
        }))

    def claims(self, qid: str) -> WikidataClaims:
        return _parse_claims(self._get_json({
            "action": "wbgetentities", "ids": qid, "props": "claims|aliases",
            "languages": "en", "format": "json",
        }))


def build_wikidata() -> WikidataClient:
    return WikidataAdapter(build_http())
```

- [ ] **Step 4: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_wikidata.py -q` — PASS (3 tests).
```bash
git add src/bellwether/discovery/wikidata.py tests/discovery/test_wikidata.py
git commit -m "feat: Wikidata client (pure parsers + adapter + build)"
```

---

### Task 7: WebSearch (Tavily) + XVerifier

**Files:**
- Create: `src/bellwether/discovery/websearch.py`, `src/bellwether/discovery/xverify.py`
- Test: `tests/discovery/test_websearch.py`, `tests/discovery/test_xverify.py`

**Interfaces:**
- Produces: `_parse_tavily(payload) -> list[SearchResult]`; `TavilyAdapter` (POST to Tavily via injected `HttpClient`-like `post`, or `httpx`; here use a small `post_json` on the adapter reading `TAVILY_API_KEY` from env; no key → `search` returns `[]`); `build_web_search() -> WebSearch`. And `XVerifier` adapter: `build_x_verifier() -> XVerifier` returning an adapter whose `verify` returns `None` when `X_API_KEY` is unset; `_parse_x(payload) -> XStatus`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/discovery/test_websearch.py
from bellwether.discovery.websearch import _parse_tavily, build_web_search


def test_parse_tavily():
    payload = {"results": [
        {"title": "T1", "url": "https://a.com", "content": "snippet one"},
        {"title": "T2", "url": "https://b.com", "content": "snippet two"},
    ]}
    rs = _parse_tavily(payload)
    assert [r.url for r in rs] == ["https://a.com", "https://b.com"]
    assert rs[0].snippet == "snippet one"


def test_build_web_search_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    ws = build_web_search()
    assert ws.search("anything") == []   # keyless -> gap-fill skipped
```

```python
# tests/discovery/test_xverify.py
from bellwether.discovery.xverify import _parse_x, build_x_verifier
from bellwether.discovery.contracts import XStatus


def test_parse_x():
    assert _parse_x({"data": {"verified": True}}) == XStatus(exists=True, verified=True)
    assert _parse_x({}) == XStatus(exists=False, verified=False)


def test_build_x_verifier_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("X_API_KEY", raising=False)
    xv = build_x_verifier()
    assert xv.verify("jack") is None   # unavailable without a key
```

- [ ] **Step 2: Run tests → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_websearch.py tests/discovery/test_xverify.py -v` — FAIL.

- [ ] **Step 3: Write `websearch.py`**

```python
# src/bellwether/discovery/websearch.py
import json
import os
import urllib.request
from bellwether.discovery.contracts import SearchResult, WebSearch, DiscoveryError

_ENDPOINT = "https://api.tavily.com/search"


def _parse_tavily(payload: dict) -> list[SearchResult]:
    return [
        SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
        for r in payload.get("results", [])
    ]


class TavilyAdapter:
    def __init__(self, api_key: str | None, timeout: float = 10.0):
        self._api_key = api_key
        self._timeout = timeout

    def search(self, query: str) -> list[SearchResult]:
        if not self._api_key:
            return []
        body = json.dumps({"api_key": self._api_key, "query": query, "max_results": 5}).encode()
        req = urllib.request.Request(_ENDPOINT, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise DiscoveryError("tavily request failed") from exc
        return _parse_tavily(payload)


def build_web_search() -> WebSearch:
    return TavilyAdapter(os.environ.get("TAVILY_API_KEY"))
```

- [ ] **Step 4: Write `xverify.py`**

```python
# src/bellwether/discovery/xverify.py
import json
import os
import urllib.request
from bellwether.discovery.contracts import XStatus, XVerifier, DiscoveryError


def _parse_x(payload: dict) -> XStatus:
    data = payload.get("data") or {}
    return XStatus(exists=bool(data), verified=bool(data.get("verified", False)))


class XVerifierAdapter:
    def __init__(self, api_key: str | None, timeout: float = 10.0):
        self._api_key = api_key
        self._timeout = timeout

    def verify(self, handle: str) -> XStatus | None:
        if not self._api_key:
            return None
        url = f"https://api.twitter.com/2/users/by/username/{handle.lstrip('@')}?user.fields=verified"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise DiscoveryError("x verify request failed") from exc
        return _parse_x(payload)


def build_x_verifier() -> XVerifier:
    return XVerifierAdapter(os.environ.get("X_API_KEY"))
```

- [ ] **Step 5: Run tests → pass; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_websearch.py tests/discovery/test_xverify.py -v` — PASS (4 tests).
```bash
git add src/bellwether/discovery/websearch.py src/bellwether/discovery/xverify.py tests/discovery/test_websearch.py tests/discovery/test_xverify.py
git commit -m "feat: Tavily WebSearch + XVerifier seams (keyless-degrade)"
```

---

### Task 8: Discovery module (DSPy)

**Files:**
- Create: `src/bellwether/discovery/discoverer.py`
- Test: `tests/discovery/test_discoverer.py`

**Interfaces:**
- Consumes: `WikidataEntity`, `SearchResult`, `Disambiguation`, `SourceCandidate`, `Discoverer` (Task 3); `dspy`; `make_lm`/`get_settings`.
- Produces: `DisambiguateSig`, `GapfillSig` DSPy signatures; `Discovery(dspy.Module)`; `_DiscovererAdapter` (maps predictions onto the contract, exposes `.model`/`.version`); `build_discoverer(lm=None, program_state=None, version="baseline") -> Discoverer`.

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_discoverer.py
import dspy
from bellwether.discovery.discoverer import build_discoverer
from bellwether.discovery.contracts import WikidataEntity, SearchResult, Disambiguation, SourceCandidate


def test_disambiguate_with_dummy_lm():
    lm = dspy.utils.DummyLM([{"qid": "Q13129", "confidence": "0.92"}])
    d = build_discoverer(lm=lm)
    out = d.disambiguate("Jerome Powell", [WikidataEntity("Q13129", "Jerome Powell", "Fed chair")])
    assert isinstance(out, Disambiguation) and out.qid == "Q13129" and abs(out.confidence - 0.92) < 1e-6


def test_gapfill_with_dummy_lm():
    lm = dspy.utils.DummyLM([{"candidates": '[{"connector_type": "rss", "config": {"feed_url": "https://x.com/feed"}, "rationale": "official blog"}]'}])
    d = build_discoverer(lm=lm)
    out = d.gapfill("Jerome Powell", ["https://federalreserve.gov"],
                    [SearchResult("Blog", "https://x.com/feed", "posts")])
    assert len(out) == 1 and isinstance(out[0], SourceCandidate)
    assert out[0].connector_type == "rss" and out[0].config == {"feed_url": "https://x.com/feed"}
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_discoverer.py -v` — FAIL.

- [ ] **Step 3: Write `discoverer.py`**

Note: DummyLM output shape must match the signature field names. Calibrate the exact `DummyLM` payload against the real signature during Step 3 (this is expected iteration, like prior DSPy tasks — adjust the test's DummyLM dict to the signature's output field names if the first run mismatches).
```python
# src/bellwether/discovery/discoverer.py
import json
import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.discovery.contracts import (
    WikidataEntity, SearchResult, Disambiguation, SourceCandidate, Discoverer,
)


class DisambiguateSig(dspy.Signature):
    """Pick which candidate entity the typed name refers to (or none)."""
    name: str = dspy.InputField()
    candidates: str = dspy.InputField(desc="JSON list of {qid,label,description}")
    qid: str = dspy.OutputField(desc="the chosen QID, or empty if none fit")
    confidence: float = dspy.OutputField(desc="0.0-1.0 confidence in the choice")


class GapfillSig(dspy.Signature):
    """Propose additional public sources for a figure that the known set lacks."""
    figure_name: str = dspy.InputField()
    known_sources: str = dspy.InputField(desc="JSON list of known source URLs")
    search_results: str = dspy.InputField(desc="JSON list of {title,url,snippet}")
    candidates: str = dspy.OutputField(desc='JSON list of {connector_type, config, rationale}')


class Discovery(dspy.Module):
    def __init__(self):
        super().__init__()
        self.disamb = dspy.Predict(DisambiguateSig)
        self.gap = dspy.Predict(GapfillSig)

    def forward_disambiguate(self, name, candidates):
        return self.disamb(name=name, candidates=candidates)

    def forward_gapfill(self, figure_name, known_sources, search_results):
        return self.gap(figure_name=figure_name, known_sources=known_sources, search_results=search_results)


class _DiscovererAdapter:
    def __init__(self, module: Discovery, model: str, version: str):
        self._m = module
        self.model = model
        self.version = version

    def disambiguate(self, name: str, candidates: list[WikidataEntity]) -> Disambiguation:
        payload = json.dumps([{"qid": c.qid, "label": c.label, "description": c.description} for c in candidates])
        pred = self._m.forward_disambiguate(name=name, candidates=payload)
        qid = (pred.qid or "").strip() or None
        return Disambiguation(qid=qid, confidence=float(pred.confidence))

    def gapfill(self, figure_name: str, known: list[str], results: list[SearchResult]) -> list[SourceCandidate]:
        pred = self._m.forward_gapfill(
            figure_name=figure_name,
            known_sources=json.dumps(known),
            search_results=json.dumps([{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results]),
        )
        try:
            raw = json.loads(pred.candidates)
        except (json.JSONDecodeError, TypeError):
            return []
        out = []
        for c in raw if isinstance(raw, list) else []:
            if isinstance(c, dict) and "connector_type" in c and "config" in c:
                out.append(SourceCandidate(connector_type=str(c["connector_type"]),
                                           config=dict(c["config"]), rationale=str(c.get("rationale", ""))))
        return out


def build_discoverer(lm=None, program_state: dict | None = None, version: str = "baseline") -> Discoverer:
    settings = get_settings()
    module = Discovery()
    module.set_lm(lm or make_lm(settings.discovery_model))
    if program_state is not None:
        module.load_state(program_state)
    return _DiscovererAdapter(module, settings.discovery_model, version)
```

- [ ] **Step 4: Run test → pass (calibrate DummyLM if needed); commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_discoverer.py -v` — PASS (2 tests).
```bash
git add src/bellwether/discovery/discoverer.py tests/discovery/test_discoverer.py
git commit -m "feat: DSPy Discovery module (disambiguate + gapfill) behind Discoverer contract"
```

---

### Task 9: Discovery pipeline (`run_discovery`)

**Files:**
- Create: `src/bellwether/discovery/pipeline.py`
- Test: `tests/discovery/test_pipeline.py`

**Interfaces:**
- Consumes: all Task 3 contracts; `score_binding` (Task 4); `youtube_feed_url`/`x_binding`/`domain_of`/`discover_feed_links` (Task 5); `Figure`/`Source` models; `get_settings`.
- Produces: `run_discovery(session, figure, *, wikidata, web_search, x_verifier, discoverer, http) -> None` — resolves the figure (search → disambiguate → claims), maps claims to bindings, gap-fills via web_search+discoverer, computes signals + `score_binding`, sets `figure.wikidata_id`/`aliases`, and **upserts** `sources` (dedup by `figure_id`+`connector_type`+config identity), setting `status`/`verified`/`discovery_confidence`/`discovery_meta`/`enabled`/`origin="discovered"`. Never modifies `origin="manual"` sources. Raises nothing for "no match" (terminal); lets `DiscoveryError` propagate (retryable).

- [ ] **Step 1: Write the failing test**

```python
# tests/discovery/test_pipeline.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.discovery.pipeline import run_discovery
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, XStatus, Disambiguation, SourceCandidate, FetchResult,
)


class StubWikidata:
    def search(self, name): return [WikidataEntity("Q1", name, "desc")]
    def claims(self, qid):
        return WikidataClaims(website="https://fed.gov", x_username="fed",
                              youtube_channel="UCabc", aliases=["Jay"])


class StubWeb:
    def search(self, query): return []


class StubX:
    def verify(self, handle): return None   # no key


class StubDiscoverer:
    def disambiguate(self, name, candidates): return Disambiguation(qid="Q1", confidence=0.95)
    def gapfill(self, figure_name, known, results): return []


class StubHttp:
    def get(self, url):
        # website has an RSS feed link; feed reachable
        if url == "https://fed.gov":
            return FetchResult(ok=True, text='<link rel="alternate" type="application/rss+xml" href="https://fed.gov/feed">')
        return FetchResult(ok=True, text="<rss></rss>")


def _figure(db_session):
    f = Figure(name="Jerome Powell", type="individual", aliases=[], owner_id=None,
               discovery_status="running")
    db_session.add(f); db_session.flush()
    return f


def test_run_discovery_creates_verified_and_pending(db_session):
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                  x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
    db_session.flush()
    assert f.wikidata_id == "Q1" and "Jay" in f.aliases
    srcs = {s.connector_type: s for s in db_session.execute(
        select(Source).where(Source.figure_id == f.id)).scalars()}
    # website feed: wikidata(0.6)+domain_match(0.3, feed on official domain)+reachable(0.2) -> active
    assert srcs["rss"].status == "active" and srcs["rss"].verified is True and srcs["rss"].enabled is True
    # X handle: wikidata only (0.6), no key -> pending_review
    assert srcs["x"].status == "pending_review" and srcs["x"].enabled is False
    assert srcs["x"].discovery_meta["wikidata"] is True


def test_run_discovery_is_idempotent(db_session):
    f = _figure(db_session)
    for _ in range(2):
        run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                      x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
        db_session.flush()
    n = len(db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all())
    assert n == 2   # one rss + one x, no duplicates on re-run
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/discovery/test_pipeline.py -v` — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `pipeline.py`**

```python
# src/bellwether/discovery/pipeline.py
from urllib.parse import urljoin
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.discovery.contracts import SourceBinding
from bellwether.discovery.verify import score_binding
from bellwether.discovery.connectors import youtube_feed_url, x_binding, domain_of, discover_feed_links


def _identity(connector_type: str, config: dict) -> str:
    if connector_type == "rss":
        return config.get("feed_url", "")
    if connector_type == "x":
        return config.get("handle", "")
    return str(sorted(config.items()))


def _feed_for_website(website: str, http) -> str | None:
    res = http.get(website)
    if res.ok and res.text:
        links = discover_feed_links(res.text)
        if links:
            return urljoin(website, links[0])
    for path in ("/feed", "/rss", "/feed.xml", "/rss.xml"):
        candidate = urljoin(website, path)
        probe = http.get(candidate)
        if probe.ok and probe.text and ("<rss" in probe.text or "<feed" in probe.text):
            return candidate
    return None


def _reachable(url: str, http) -> bool:
    res = http.get(url)
    return bool(res.ok and res.text and ("<rss" in res.text or "<feed" in res.text))


def run_discovery(session: Session, figure: Figure, *, wikidata, web_search, x_verifier, discoverer, http) -> None:
    settings = get_settings()
    threshold = settings.discovery_confidence_threshold

    candidates = wikidata.search(figure.name)
    official_domain = None
    bindings: list[SourceBinding] = []

    if candidates:
        disamb = discoverer.disambiguate(figure.name, candidates)
        qid = disamb.qid or candidates[0].qid
        ambiguous = disamb.qid is None or disamb.confidence < 0.5
        figure.wikidata_id = qid
        claims = wikidata.claims(qid)
        if claims.aliases:
            figure.aliases = sorted(set(list(figure.aliases) + claims.aliases))
        if claims.website:
            official_domain = domain_of(claims.website)

        # website -> rss feed
        if claims.website:
            feed = _feed_for_website(claims.website, http)
            if feed:
                signals = {"wikidata": True, "domain_match": domain_of(feed) == official_domain,
                           "reachable": _reachable(feed, http)}
                bindings.append(_binding("rss", {"feed_url": feed}, signals, threshold, ambiguous))
        # youtube -> rss feed
        if claims.youtube_channel:
            feed = youtube_feed_url(claims.youtube_channel)
            signals = {"wikidata": True, "reachable": _reachable(feed, http)}
            bindings.append(_binding("rss", {"feed_url": feed}, signals, threshold, ambiguous))
        # x handle
        if claims.x_username:
            ct, cfg = x_binding(claims.x_username)
            xs = x_verifier.verify(claims.x_username)
            signals = {"wikidata": True,
                       "domain_match": False,
                       "x_verified": bool(xs and xs.verified)}
            bindings.append(_binding(ct, cfg, signals, threshold, ambiguous))

        # gap-fill (LLM + web search) — proposals, must pass verification
        results = web_search.search(f"{figure.name} official blog rss feed")
        known = [claims.website] if claims.website else []
        for cand in discoverer.gapfill(figure.name, known, results):
            reachable = _reachable(cand.config.get("feed_url", ""), http) if cand.connector_type == "rss" else False
            signals = {"domain_match": official_domain is not None and
                       domain_of(cand.config.get("feed_url", "")) == official_domain,
                       "reachable": reachable}
            bindings.append(_binding(cand.connector_type, cand.config, signals, threshold, ambiguous, source="tavily"))

    _upsert(session, figure, bindings)


def _binding(connector_type, config, signals, threshold, ambiguous, source="wikidata") -> SourceBinding:
    confidence, verified = score_binding(signals, threshold)
    active = verified and not ambiguous
    meta = {"source": source, **{k: signals.get(k) for k in ("wikidata", "domain_match", "x_verified", "reachable")}}
    return SourceBinding(
        connector_type=connector_type, config=config, origin="discovered",
        status="active" if active else "pending_review", verified=verified,
        discovery_confidence=confidence, discovery_meta=meta, enabled=active,
    )


def _upsert(session: Session, figure: Figure, bindings: list[SourceBinding]) -> None:
    existing = {(_identity(s.connector_type, s.config)): s for s in session.execute(
        select(Source).where(Source.figure_id == figure.id, Source.origin == "discovered")).scalars()}
    for b in bindings:
        key = _identity(b.connector_type, b.config)
        row = existing.get(key)
        if row is None:
            session.add(Source(
                figure_id=figure.id, connector_type=b.connector_type, config=b.config,
                provenance="primary", origin="discovered", enabled=b.enabled,
                status=b.status, verified=b.verified,
                discovery_confidence=b.discovery_confidence, discovery_meta=b.discovery_meta,
                owner_id=figure.owner_id,
            ))
        else:  # re-run: refresh scores, but never override a human review decision
            if row.status != "rejected":
                row.status, row.verified = b.status, b.verified
                row.discovery_confidence, row.discovery_meta = b.discovery_confidence, b.discovery_meta
                row.enabled = b.enabled
```

- [ ] **Step 4: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/discovery/test_pipeline.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/discovery/pipeline.py tests/discovery/test_pipeline.py
git commit -m "feat: run_discovery pipeline (resolve -> map -> verify -> upsert sources)"
```

---

### Task 10: Queue harness for figures

**Files:**
- Modify: `src/bellwether/queue.py`
- Test: `tests/test_queue_discovery.py`

**Interfaces:**
- Consumes: `Figure` model.
- Produces: `claim_pending_figure(session, to_status="running") -> Figure | None` (FOR UPDATE SKIP LOCKED on `discovery_status=="pending"`, oldest first, sets `discovery_status=to_status` + `discovery_claimed_at=now`); `reclaim_stale_figures(session, in_status, to_status, older_than_seconds) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_discovery.py
from datetime import datetime, timedelta, timezone
from bellwether.models.figure import Figure
from bellwether.queue import claim_pending_figure, reclaim_stale_figures


def _fig(db_session, status="pending"):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None, discovery_status=status)
    db_session.add(f); db_session.flush()
    return f


def test_claim_pending_figure(db_session):
    f = _fig(db_session)
    claimed = claim_pending_figure(db_session)
    assert claimed.id == f.id and claimed.discovery_status == "running" and claimed.discovery_claimed_at is not None
    assert claim_pending_figure(db_session) is None   # nothing left pending


def test_reclaim_stale_figures(db_session):
    f = _fig(db_session, status="running")
    f.discovery_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    db_session.flush()
    n = reclaim_stale_figures(db_session, "running", "pending", 300)
    assert n == 1
    db_session.refresh(f)
    assert f.discovery_status == "pending" and f.discovery_claimed_at is None
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_queue_discovery.py -v` — FAIL.

- [ ] **Step 3: Add to `queue.py`**

```python
# append to src/bellwether/queue.py  (imports: Figure, plus existing select/update/datetime/timezone/timedelta)
from bellwether.models.figure import Figure


def claim_pending_figure(session: Session, to_status: str = "running") -> Figure | None:
    figure = session.execute(
        select(Figure).where(Figure.discovery_status == "pending")
        .order_by(Figure.id).with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if figure is None:
        return None
    figure.discovery_status = to_status
    figure.discovery_claimed_at = datetime.now(timezone.utc)
    return figure


def reclaim_stale_figures(session: Session, in_status: str, to_status: str,
                          older_than_seconds: float) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Figure).where(Figure.discovery_status == in_status, Figure.discovery_claimed_at < cutoff)
        .values(discovery_status=to_status, discovery_claimed_at=None)
    )
    return result.rowcount
```
(Confirm `select`, `update`, `datetime`, `timedelta`, `timezone`, `Session` are already imported at the top of `queue.py`; add any missing.)

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/test_queue_discovery.py -v` — PASS (2 tests).
```bash
git add src/bellwether/queue.py tests/test_queue_discovery.py
git commit -m "feat: figure discovery queue (claim_pending_figure/reclaim_stale_figures)"
```

---

### Task 11: Discovery worker stage + CLI

**Files:**
- Modify: `src/bellwether/worker.py`
- Test: `tests/test_worker_discovery.py`

**Interfaces:**
- Consumes: `Stage`, `run_worker` (worker.py); `claim_pending_figure`/`reclaim_stale_figures` (Task 10); `run_discovery` (Task 9); the `build_*` factories (Tasks 6-8) + `build_http`.
- Produces: `make_discovery_stage(*, wikidata, web_search, x_verifier, discoverer, http) -> Stage` (claim figure → `run_discovery` → set `discovery_status="done"`; on `DiscoveryError` set `discovery_status="failed"` + `discovery_error`, commit — retryable via reclaim); `_build_stage("discovery")` wires the real clients; CLI `choices=[…, "discovery"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_discovery.py
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.worker import make_discovery_stage
from bellwether.discovery.contracts import WikidataEntity, WikidataClaims, Disambiguation, FetchResult, DiscoveryError


class W:
    def search(self, name): return [WikidataEntity("Q1", name, "d")]
    def claims(self, qid): return WikidataClaims("https://fed.gov", "fed", None, [])
class Web:
    def search(self, q): return []
class X:
    def verify(self, h): return None
class D:
    def disambiguate(self, n, c): return Disambiguation("Q1", 0.9)
    def gapfill(self, *a): return []
class Http:
    def get(self, url):
        return FetchResult(True, '<link rel="alternate" type="application/rss+xml" href="https://fed.gov/feed">' if url == "https://fed.gov" else "<rss></rss>")


def test_discovery_stage_processes_figure(db_session):
    f = Figure(name="Jerome Powell", type="individual", aliases=[], owner_id=None, discovery_status="pending")
    db_session.add(f); db_session.flush()
    stage = make_discovery_stage(wikidata=W(), web_search=Web(), x_verifier=X(), discoverer=D(), http=Http())
    claimed = stage.claim_next(db_session)
    stage.process(db_session, claimed)
    db_session.refresh(f)
    assert f.discovery_status == "done"
    assert db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().first() is not None


def test_discovery_stage_marks_failed_on_error(db_session):
    class BadW:
        def search(self, name): raise DiscoveryError("wikidata down")
        def claims(self, qid): raise DiscoveryError("x")
    f = Figure(name="X", type="individual", aliases=[], owner_id=None, discovery_status="pending")
    db_session.add(f); db_session.flush()
    stage = make_discovery_stage(wikidata=BadW(), web_search=Web(), x_verifier=X(), discoverer=D(), http=Http())
    stage.process(db_session, stage.claim_next(db_session))
    db_session.refresh(f)
    assert f.discovery_status == "failed" and "wikidata down" in (f.discovery_error or "")
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_worker_discovery.py -v` — FAIL.

- [ ] **Step 3: Add `make_discovery_stage` + wiring**

In `src/bellwether/worker.py` add imports (`claim_pending_figure`, `reclaim_stale_figures` from `bellwether.queue`; `run_discovery` from `bellwether.discovery.pipeline`; `DiscoveryError` from `bellwether.discovery.contracts`) and:
```python
def make_discovery_stage(*, wikidata, web_search, x_verifier, discoverer, http) -> Stage:
    def process(session, figure) -> None:
        try:
            run_discovery(session, figure, wikidata=wikidata, web_search=web_search,
                          x_verifier=x_verifier, discoverer=discoverer, http=http)
            figure.discovery_status = "done"
            figure.discovery_error = None
        except DiscoveryError as exc:
            figure.discovery_status = "failed"
            figure.discovery_error = str(exc)
        figure.discovery_claimed_at = None
        session.commit()

    return Stage(
        name="discovery",
        claim_next=lambda s: claim_pending_figure(s, "running"),
        reclaim=lambda s, secs: reclaim_stale_figures(s, "running", "pending", secs),
        process=process,
    )
```
In `_build_stage`, add before the final `return`:
```python
    if name == "discovery":
        from bellwether.discovery.wikidata import build_wikidata
        from bellwether.discovery.websearch import build_web_search
        from bellwether.discovery.xverify import build_x_verifier
        from bellwether.discovery.discoverer import build_discoverer
        from bellwether.discovery.http import build_http
        return make_discovery_stage(wikidata=build_wikidata(), web_search=build_web_search(),
                                    x_verifier=build_x_verifier(), discoverer=build_discoverer(),
                                    http=build_http())
```
(The `build_*` imports are **local to this branch** so `worker.py`'s module top stays free of Wikidata/Tavily/DSPy imports.)
Update the CLI: `parser.add_argument("stage", choices=["detect", "extract", "resolve", "measure", "discovery"])` and the final log line still works (`processed %d`).

- [ ] **Step 4: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/test_worker_discovery.py tests/test_worker.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/worker.py tests/test_worker_discovery.py
git commit -m "feat: discovery worker stage + CLI (python -m bellwether.worker discovery)"
```

---

### Task 12: X ingestion connector

**Files:**
- Create: `src/bellwether/connectors/x.py`
- Modify: `src/bellwether/connectors/registry.py`
- Test: `tests/connectors/test_x.py`

**Interfaces:**
- Consumes: `RawItem` (`connectors/base.py`), `Source`.
- Produces: `_parse_x_timeline(payload, handle) -> list[RawItem]` (pure); `XConnector(handle, api_key)` with `fetch() -> list[RawItem]` (returns `[]` when no key); `build_connector` maps `connector_type=="x"` → `XConnector(config["handle"], os.environ.get("X_API_KEY"))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/connectors/test_x.py
from bellwether.connectors.x import _parse_x_timeline, XConnector


def test_parse_x_timeline():
    payload = {"data": [
        {"id": "1", "text": "hello", "created_at": "2026-07-01T12:00:00.000Z"},
        {"id": "2", "text": "world", "created_at": "2026-07-02T09:30:00.000Z"},
    ]}
    items = _parse_x_timeline(payload, "jack")
    assert [i.external_id for i in items] == ["1", "2"]
    assert items[0].text == "hello" and items[0].url == "https://x.com/jack/status/1"
    assert items[0].published_at.year == 2026


def test_connector_disabled_without_key():
    assert XConnector("jack", api_key=None).fetch() == []
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/connectors/test_x.py -v` — FAIL.

- [ ] **Step 3: Write `x.py`**

```python
# src/bellwether/connectors/x.py
import json
import urllib.request
from datetime import datetime, timezone
from bellwether.connectors.base import RawItem


def _parse_x_timeline(payload: dict, handle: str) -> list[RawItem]:
    items: list[RawItem] = []
    for t in payload.get("data", []):
        tid = t.get("id")
        created = t.get("created_at")
        if not tid or not created:
            continue
        published_at = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
        items.append(RawItem(external_id=str(tid), text=t.get("text", ""),
                             url=f"https://x.com/{handle}/status/{tid}", published_at=published_at))
    return items


class XConnector:
    def __init__(self, handle: str, api_key: str | None):
        self.handle = handle.lstrip("@")
        self.api_key = api_key

    def fetch(self) -> list[RawItem]:
        if not self.api_key:
            return []
        url = (f"https://api.twitter.com/2/tweets/search/recent"
               f"?query=from:{self.handle}&tweet.fields=created_at&max_results=20")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []
        return _parse_x_timeline(payload, self.handle)
```

- [ ] **Step 4: Register in `registry.py`**

```python
# src/bellwether/connectors/registry.py — add import + branch
import os
from bellwether.connectors.x import XConnector
# ... inside build_connector, before the raise:
    if source.connector_type == "x":
        return XConnector(source.config["handle"], os.environ.get("X_API_KEY"))
```

- [ ] **Step 5: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/connectors/test_x.py -v` — PASS (2 tests). (Create `tests/connectors/__init__.py` if the package dir doesn't exist.)
```bash
git add src/bellwether/connectors/x.py src/bellwether/connectors/registry.py tests/connectors/test_x.py
git commit -m "feat: X ingestion connector (disabled without X_API_KEY) + registry"
```

---

### Task 13: Figure-add discovery trigger API

**Files:**
- Modify: `src/bellwether/api/watchlist.py`, `src/bellwether/api/schemas.py`, the figures repository (`src/bellwether/repositories/figures.py` — confirm the exact path used by `watchlist.py`'s `repo`)
- Test: `tests/api/test_discovery_trigger.py`

**Interfaces:**
- Consumes: `create_figure` repo fn; `get_current_user`/`get_session`; `Figure`.
- Produces: `FigureCreate` gains `discover: bool = True`; `create_figure(...)` sets `discovery_status="pending"` when `discover` else `"skipped"`; `FigureRead` gains `discovery_status`, `wikidata_id`; `SourceRead` gains `status`, `verified`, `discovery_confidence`; new `POST /figures/{figure_id}/discover` → sets `discovery_status="pending"`, 404 if not owned, returns the figure.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_discovery_trigger.py
from sqlalchemy import select
from bellwether.models.figure import Figure


def test_create_figure_defaults_to_discovery_pending(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Jerome Powell", "type": "individual"}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["discovery_status"] == "pending"


def test_create_figure_discover_false_skips(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Manual Co", "type": "org", "discover": False}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["discovery_status"] == "skipped"


def test_retrigger_discovery(client, auth_headers, db_session):
    fid = client.post("/figures", json={"name": "X", "type": "individual", "discover": False},
                      headers=auth_headers).json()["id"]
    r = client.post(f"/figures/{fid}/discover", headers=auth_headers)
    assert r.status_code == 200 and r.json()["discovery_status"] == "pending"
    assert client.post("/figures/999999/discover", headers=auth_headers).status_code == 404
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/api/test_discovery_trigger.py -v` — FAIL.

- [ ] **Step 3: Extend schemas**

In `src/bellwether/api/schemas.py`: add `discover: bool = True` to `FigureCreate`; add `discovery_status: str` and `wikidata_id: str | None` to `FigureRead`; add `status: str`, `verified: bool`, `discovery_confidence: float | None` to `SourceRead`.

- [ ] **Step 4: Set discovery_status on create + add the trigger route**

In the figures repo `create_figure`, accept + set `discovery_status` (`"pending"` if `discover` else `"skipped"`). In `src/bellwether/api/watchlist.py`, pass `body.discover` through, and add:
```python
@router.post("/figures/{figure_id}/discover", response_model=FigureRead)
def trigger_discovery(figure_id: int, session: Session = Depends(get_session),
                      user: User = Depends(get_current_user)):
    figure = repo.get_figure(session, figure_id, owner_id=user.id)
    if figure is None:
        raise HTTPException(status_code=404, detail="Figure not found")
    figure.discovery_status = "pending"
    figure.discovery_claimed_at = None
    session.flush()
    return figure
```
(Import `HTTPException` if not already.)

- [ ] **Step 5: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/api/test_discovery_trigger.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/api/watchlist.py src/bellwether/api/schemas.py src/bellwether/repositories tests/api/test_discovery_trigger.py
git commit -m "feat: figure-add discovery trigger (discover flag + /figures/{id}/discover)"
```

---

### Task 14: Review queue API

**Files:**
- Create: `src/bellwether/api/discovery.py`
- Modify: `src/bellwether/api/schemas.py`, `src/bellwether/api/app.py`
- Test: `tests/api/test_discovery_review.py`

**Interfaces:**
- Consumes: `get_session`/`get_current_user`/`User`/`Figure`/`Source`.
- Produces (schemas): `DiscoveryQueueItem{source_id, figure_id, figure_name, connector_type, config, discovery_confidence, discovery_meta}`, `DiscoveryDecision{decision: str}`.
- Produces (router, authenticated, owner-scoped): `GET /discovery/queue?limit=` → `pending_review` sources for the caller's figures; `POST /discovery/{source_id}` `{decision:"confirm"|"reject"}` → confirm sets `status="active"/enabled=true/verified=true`, reject sets `status="rejected"/enabled=false`; 404 if not owned; 422 on an unknown decision.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_discovery_review.py
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.user import User
from bellwether.repositories.users import get_user_by_username


def _pending_source(db_session, owner_id):
    f = Figure(name="F", type="individual", aliases=[], owner_id=owner_id, discovery_status="done")
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="x", config={"handle": "fed"}, provenance="primary",
               origin="discovered", enabled=False, status="pending_review", verified=False,
               discovery_confidence=0.6, discovery_meta={"wikidata": True}, owner_id=owner_id)
    db_session.add(s); db_session.flush()
    return s


def test_queue_requires_auth(client):
    assert client.get("/discovery/queue").status_code == 401


def test_queue_and_confirm(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    s = _pending_source(db_session, uid)
    q = client.get("/discovery/queue", headers=auth_headers).json()
    assert any(i["source_id"] == s.id for i in q)
    r = client.post(f"/discovery/{s.id}", json={"decision": "confirm"}, headers=auth_headers)
    assert r.status_code == 200
    db_session.refresh(s)
    assert s.status == "active" and s.enabled is True and s.verified is True


def test_reject_and_ownership(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    s = _pending_source(db_session, uid)
    assert client.post(f"/discovery/{s.id}", json={"decision": "reject"}, headers=auth_headers).status_code == 200
    db_session.refresh(s)
    assert s.status == "rejected" and s.enabled is False
    # other user's source -> 404
    other = User(username="other", hashed_password="x", is_active=True); db_session.add(other); db_session.flush()
    s2 = _pending_source(db_session, other.id)
    assert client.post(f"/discovery/{s2.id}", json={"decision": "confirm"}, headers=auth_headers).status_code == 404
    # bad decision -> 422
    assert client.post(f"/discovery/{s.id}", json={"decision": "maybe"}, headers=auth_headers).status_code == 422
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/api/test_discovery_review.py -v` — FAIL.

- [ ] **Step 3: Add schemas**

In `src/bellwether/api/schemas.py`:
```python
class DiscoveryQueueItem(BaseModel):
    source_id: int
    figure_id: int
    figure_name: str
    connector_type: str
    config: dict
    discovery_confidence: float | None
    discovery_meta: dict | None


class DiscoveryDecision(BaseModel):
    decision: str
```

- [ ] **Step 4: Write the router**

```python
# src/bellwether/api/discovery.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.api.schemas import DiscoveryQueueItem, DiscoveryDecision

router = APIRouter()


@router.get("/discovery/queue", response_model=list[DiscoveryQueueItem])
def discovery_queue(limit: int = Query(default=50, ge=1, le=500),
                    session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    rows = session.execute(
        select(Source, Figure).join(Figure, Figure.id == Source.figure_id)
        .where(Figure.owner_id == user.id, Source.status == "pending_review")
        .order_by(Source.id).limit(limit)
    ).all()
    return [DiscoveryQueueItem(
        source_id=s.id, figure_id=f.id, figure_name=f.name, connector_type=s.connector_type,
        config=s.config, discovery_confidence=s.discovery_confidence, discovery_meta=s.discovery_meta,
    ) for s, f in rows]


@router.post("/discovery/{source_id}")
def review_source(source_id: int, body: DiscoveryDecision,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if body.decision not in ("confirm", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'confirm' or 'reject'")
    source = session.execute(
        select(Source).join(Figure, Figure.id == Source.figure_id)
        .where(Source.id == source_id, Figure.owner_id == user.id)
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.decision == "confirm":
        source.status, source.enabled, source.verified = "active", True, True
    else:
        source.status, source.enabled = "rejected", False
    return {"ok": True}
```

- [ ] **Step 5: Wire the router**

In `src/bellwether/api/app.py`, add `from bellwether.api.discovery import router as discovery_router` and `app.include_router(discovery_router)` in `create_app`.

- [ ] **Step 6: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/api/test_discovery_review.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/api/discovery.py src/bellwether/api/schemas.py src/bellwether/api/app.py tests/api/test_discovery_review.py
git commit -m "feat: discovery review queue API (queue + confirm/reject)"
```

---

## Self-Review

**Spec coverage (Plan 6 spec):**
- Discovery columns on figures + sources (§4) — Task 1 ✓.
- Config (§10) — Task 2 ✓.
- Contracts/DTOs + seams (§5) — Task 3 (+ http seam) ✓.
- Deterministic confidence model (§8) — Task 4 ✓ (weights + threshold + the 1-signal-pending / 2-signal-active boundary).
- Connector mapping incl. website feed-discovery (§5, §7) — Task 5 ✓.
- Wikidata backbone (§3, §5) — Task 6 ✓.
- Tavily WebSearch + XVerifier keyless-degrade (§5) — Task 7 ✓.
- DSPy Discovery module behind `Discoverer` contract (§5, §6) — Task 8 ✓ (Plan-5-optimizable via `build_discoverer(program_state=…)`).
- `run_discovery` orchestration + dedup + ambiguous/no-match paths (§3, §8) — Task 9 ✓.
- Figure discovery queue (§3) — Task 10 ✓.
- Discovery worker stage + CLI; `worker.py` stays clean (§2, §9) — Task 11 ✓ (local imports in the `_build_stage` branch).
- X ingestion connector, disabled without key (§7) — Task 12 ✓.
- Figure-add trigger + re-trigger (§9) — Task 13 ✓.
- Review queue API, owner-scoped (§9) — Task 14 ✓.
- **Firewall/invariant "LLM proposes, verification disposes"** — enforced in `run_discovery` (Task 9): gap-fill candidates get no `wikidata` signal, so they can't reach 0.7 without strong corroboration; unit-backed by Task 4's `test_llm_proposal_weak_stays_pending`.
- **Deferred (spec §13):** YouTube dedicated connector, Discovery optimization, `discovery_labels` table, scheduled re-discovery — no task, correct.

**Deliberate test scoping (flag for reviewer):** live Wikidata/Tavily/X network paths + the real LLM disambiguation/gap-fill are **not** exercised in the suite (adapters are thin network+pure-parse; the parse fns are unit-tested, the pipeline/stage use stubs, the Discovery module uses `DummyLM`). A live discovery run + a `POST /discovery/{id}` commit-path smoke are the pre-merge live checks — same discipline as the market adapter and the Plan-5 review endpoint.

**Placeholder scan:** every code step shows complete code; commands have expected output. The only intentional iteration point is Task 8's `DummyLM` calibration (flagged in-step), consistent with prior DSPy tasks.

**Type consistency:** `SourceBinding`/`WikidataClaims`/`SearchResult`/`XStatus`/`Disambiguation`/`SourceCandidate`/`FetchResult` (Task 3) are consumed with the same fields in Tasks 6–9. `score_binding(signals, threshold) -> (float, bool)` (Task 4) matches its call in Task 9's `_binding`. `run_discovery(session, figure, *, wikidata, web_search, x_verifier, discoverer, http)` (Task 9) matches `make_discovery_stage` (Task 11). `claim_pending_figure`/`reclaim_stale_figures` (Task 10) match the Stage lambdas (Task 11). `build_wikidata`/`build_web_search`/`build_x_verifier`/`build_discoverer`/`build_http` (Tasks 6–8, 3) match the `_build_stage` wiring (Task 11). `x_binding -> ("x", {"handle":…})` (Task 5) matches the X connector config key (Task 12) and the pipeline identity key (Task 9). The discovery columns (Task 1) match every model access in Tasks 9–14.
