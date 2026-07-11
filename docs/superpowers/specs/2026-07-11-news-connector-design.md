# News Connector — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

The pipeline ingests a figure's own statements (their X posts, their RSS/YouTube feed) but
has no way to pull **news coverage about** a figure. A user adding a figure to the watchlist
wants relevant news polled automatically alongside the figure's own sources.

## Goal

Add a `news` connector that fetches recent news articles about a figure via Google News's
free search-RSS feed, auto-created (enabled) when a figure is added, and polled by the
existing ingest scheduler with no scheduler changes.

## Decisions (locked)

1. **Provider: Google News RSS** — `news.google.com/rss/search?q=<query>` (free, no key,
   verified live to return ~100 dated items via the SSL-fixed `RssConnector`).
2. **Auto per figure** — adding a figure auto-creates one enabled `news` source with the
   figure's name as the query; deterministic, so no discovery/LLM review gate.

## Architecture

### 1. Connector — `src/bellwether/connectors/news.py`

A thin `NewsConnector` that builds the Google News search-RSS URL and **delegates to the
existing `RssConnector`** for fetch + parse (inheriting its dedup, dating, and `SSL_CONTEXT`
handling — no duplicated feed logic):

```python
import urllib.parse
from bellwether.connectors.base import RawItem
from bellwether.connectors.rss import RssConnector


class NewsConnector:
    def __init__(self, query: str, recency_days: int = 7):
        q = urllib.parse.quote(f'"{query}" when:{recency_days}d')
        self.url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

    def fetch(self) -> list[RawItem]:
        return RssConnector(self.url).fetch()
```

- The query is phrase-quoted (`"<name>"`) for relevance and carries `when:<N>d` to bound
  each poll to recent news (see §Recency).
- Locale is hardcoded `hl=en-US&gl=US&ceid=US:en` for v1.

### 2. Registry wiring — `src/bellwether/connectors/registry.py`

- `build_connector`: add `if source.connector_type == "news": return
  NewsConnector(source.config["query"], recency_days=<setting>)`.
- Add `"news"` to `KNOWN_CONNECTOR_TYPES` (`frozenset({"rss", "x", "news"})`) so discovery
  won't reject it and it's recognized as a real, ingestable type.

The ingest scheduler (`claim_due_source` / `make_ingest_stage`) needs **no change** — it
already polls every enabled source; a `news` source is just another enabled source.

### 3. Auto-creation at figure add

In the `POST /figures` handler (where discovery is triggered today), after `create_figure`,
create one `news` source via a repo helper `create_news_source(session, figure, owner_id)`:

```
connector_type = "news"
config         = {"query": figure.name}
enabled        = True
status         = "active"
origin         = "auto"          # neither "manual" nor "discovered"
provenance     = "news"          # coverage ABOUT the figure (see §Provenance)
poll_interval_seconds = <news_poll_interval_seconds setting, default 1800>  # 30 min
```

Because the query is deterministic (name → Google News search), the source is enabled
immediately — it does **not** go through the discovery confidence gate / review queue that
LLM-proposed sources use.

### 4. Recency & volume

Google News search-RSS returns up to ~100 items. Unbounded, a figure's first poll would
create ~100 `statements` → ~100 Detect LLM calls. To bound it:

- The query carries **`when:<news_recency_days>d`** (setting, default **7**), limiting each
  poll to the last N days.
- Dedup by `(source_id, external_id)` (already enforced by `ingest_source`) means subsequent
  polls only create statements for genuinely new articles.
- `poll_interval_seconds` defaults to **1800 (30 min)** for news sources — news does not need
  5-minute polling, and a longer interval reduces churn and Google-News load.

### 5. Existing figures — backfill

Auto-creation covers figures added after this ships. A one-time backfill creates a `news`
source for any existing figure that lacks one:

```python
def backfill_news_sources(session) -> int:
    # for each figure with no connector_type="news" source, create one; returns count
```

Run once (e.g. `python -c` or a tiny CLI invocation) to give existing figures (e.g. the
current Trump figure) news coverage.

### 6. Provenance

News is coverage *about* the figure, not authored *by* them (unlike their X/RSS). Statements
ingested from a `news` source are tagged `provenance="news"` (flows from `source.provenance`
via `ingest_source`). Detect/Extract do not branch on provenance today, so this is purely a
useful downstream label (distinguish first-party statements from press coverage). No enum
constraint on `sources.provenance` / `statements.provenance` blocks a new value.

## Settings (config.py)

- `news_recency_days: int = 7`
- `news_poll_interval_seconds: int = 1800`

Both plain `Settings` fields (provider-agnostic pattern). Google News needs no credentials.

## Data flow

```
POST /figures (add "Jerome Powell")
  -> create_figure
  -> create_news_source: news source (query="Jerome Powell", enabled, 30-min interval)
  -> ingest scheduler claims it when due
       -> NewsConnector.fetch() -> Google News RSS -> RssConnector parse
       -> dedup by (source_id, external_id) -> new statements (status="new", provenance="news")
  -> detect -> extract -> resolve -> measure   (existing pipeline)
```

## Error handling

- A failed Google News fetch is handled exactly like any source failure by the ingest stage:
  `RssConnector.fetch` errors propagate, `ingest_source`'s per-source guard (in
  `run_ingest_pass`) or the ingest stage's back-off (claim already stamped `last_polled_at`)
  applies. One figure's news failure never blocks other sources.
- A malformed/empty Google News response yields zero items (feedparser parses nothing) — no
  statements created, no error, retried next interval.

## Testing

- **`NewsConnector`** (unit): builds the expected Google News URL for a query (phrase-quoted +
  `when:Nd` + locale); with a stubbed `RssConnector` (or local fixture feed) `fetch()` returns
  the parsed `RawItem`s.
- **Registry** (unit): `build_connector` returns a `NewsConnector` for `connector_type="news"`;
  `"news"` is in `KNOWN_CONNECTOR_TYPES`.
- **Auto-creation** (integration, real Postgres): adding a figure via the create path yields
  exactly one enabled `news` source with `config["query"] == figure.name`, `provenance="news"`,
  and the news poll interval.
- **Backfill** (integration): a figure with no news source gets one; a figure that already has
  one does not get a duplicate.
- **Live** (before merge): poll a real Google News feed for a figure and confirm dated
  articles become `new` statements (external adapters are stubbed in the suite — verify live
  per AGENTS.md).

## Out of scope (YAGNI)

- No per-figure custom keywords/topic — the query is the figure name.
- No aliases in the query (name only for v1).
- No configurable locale/region in v1 (hardcoded `en-US`; liftable to a setting later).
- No change to the ingest scheduler, detect/extract/resolve/measure, or the discovery pipeline
  beyond adding `"news"` to `KNOWN_CONNECTOR_TYPES`.
