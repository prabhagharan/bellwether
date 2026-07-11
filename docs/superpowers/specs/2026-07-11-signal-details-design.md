# Enriched Signal Details — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

The dashboard's "Recent signals" panel shows a bare one-liner per signal —
`direction/magnitude · conf · entities` — with no context about the underlying article or
post. A user can't tell *what* the signal is about, *where* it came from, or *why* the LLM
assigned that direction. The `/signals` endpoint returns only the extraction's own fields
even though it already joins the `Statement` and `Figure` behind it.

## Goal

Enrich each signal with the source context (headline, link, source type, figure, date, and
the evidence quote) and present it as a compact, click-to-expand card.

## Decisions (locked)

1. **Show all four context groups** on each signal: headline/statement text, link to the
   source, source type + figure + date, and the evidence quote.
2. **Compact + expand-on-click** layout: a one-line summary that expands to reveal the meta
   line, link, and evidence quote.

## Architecture

### 1. Backend — enrich `/signals`

`src/bellwether/api/feed.py::signals` already joins `Statement` and `Figure`. Add one join
to `Source` and return the richer shape. Widen `SignalRead` (`src/bellwether/api/schemas.py`)
with these fields (existing fields unchanged):

| New field | Source column | Notes |
|---|---|---|
| `text` | `Statement.text` | the headline / post text |
| `url` | `Statement.url` | link to the article/post; **nullable** |
| `source_type` | `Source.connector_type` | `news` / `x` / `rss` — precise "what kind of source" (more descriptive than `provenance`, which is only news-vs-first-party); always present (see join note) |
| `figure_name` | `Figure.name` | who the signal is about |
| `published_at` | `Statement.published_at` | when |
| `evidence_quote` | `Extraction.evidence_quote` | the verbatim snippet the signal was extracted from |

Existing `SignalRead` fields kept: `id, statement_id, direction, magnitude, confidence,
entities, version`.

The endpoint currently returns `session.execute(q).scalars()` (bare `Extraction` objects).
It will instead select the joined columns it needs and build each `SignalRead` explicitly
(extraction fields + `Statement.text/url/published_at` + `Source.connector_type` +
`Figure.name`). Query params, filters (`figure_id`, `direction`, `min_confidence`), ordering
(`Extraction.id desc`), and `limit` are unchanged.

**Join note:** `Statement.source_id` is `NOT NULL` with `ON DELETE CASCADE` — deleting a
source cascades to its statements, so every statement always has a live source. A plain
inner join to `Source` is correct, and `source_type` is always present (never null).

### 2. Frontend — compact, expandable signal card

In `frontend/src/app/(dash)/page.tsx`, replace the one-line `<li>` in the "Recent signals"
section with a small `SignalItem` component that holds its own `expanded` state (`useState`):

- **Compact (default):** one line — a direction/magnitude **badge**, the truncated headline,
  the confidence, and a caret (▸). Clicking the row toggles expansion.
- **Expanded:** reveals
  - a meta line: `<source_type> · <figure_name> · <published_at (short date)>`
  - the full headline, with a **↗ link** to `url` opening in a new tab (omitted when `url`
    is null)
  - the **evidence quote** as a muted italic sub-line
  - the entities

The direction badge is color-coded: `up` green, `down` red, `neutral` gray. `source_type`
renders as a small tag. All fields degrade gracefully when null (no link, no quote, etc.).

## Data flow

```
GET /signals  (joins Extraction → Statement → Figure, outer-join Source)
  -> SignalRead[] { direction, magnitude, confidence, entities,
                    text, url, source_type, figure_name, published_at, evidence_quote, ... }
  -> "Recent signals": SignalItem (compact) --click--> expanded (meta + link + quote)
```

## Error handling

- `url` null → no link rendered. `evidence_quote` and `source_type` are always present
  (`evidence_quote` is `NOT NULL`; `Statement.source_id` is `NOT NULL` + `CASCADE`, so every
  statement has a live source).

## Testing

- **Backend** (`tests/api/test_feed.py` or the existing signals test): seed a figure + source
  + statement + extraction, `GET /signals`, and assert the response carries the new fields
  with the right values (`text`, `url`, `source_type == source.connector_type`,
  `figure_name`, `published_at`, `evidence_quote`). Assert the existing fields still present.
- **Frontend** (`SignalItem` component test, vitest — same harness as
  `frontend/src/hooks/useAlertStream.test.ts`): renders the compact summary (badge + headline
  + confidence); clicking expands to show the evidence quote and the link; a null `url`
  renders no link.

## Out of scope (YAGNI)

- No pagination / "load more" (keeps the existing `limit`).
- No new filters (source-type filter, full-text search) — only the existing direction filter.
- No change to the Impacts page or the live-alerts (SSE) panel.
- No expand-all / collapse-all controls.
- No new `provenance`-based field (source_type via `connector_type` covers "what kind").
