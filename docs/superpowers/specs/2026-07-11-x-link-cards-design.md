# Enrich X Tweets with Link-Card Text — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

Many high-signal tweets are just a shortened `t.co` link with little or no text (a shared
article or livestream). The X connector stores only the tweet's raw `text` field, so a
link-only tweet becomes a statement whose text is a bare URL (e.g. `https://t.co/mxgaKQUZo1`).
The detector correctly marks that irrelevant — but the *actual* content is in the link, and
the X API already returns that content (title + description of the link card) in the same
response. We throw it away.

## Goal

Fold each link card's title + description (from the X API's `entities.urls`) into the tweet
text so link-only tweets become analyzable — with no extra API call and no external page
fetch.

## Decision (locked)

**Always** fold in every link card (no "thin tweet" heuristic). A tweet with real text keeps
it and gains the link context; a link-only tweet gets the card as its content.

## Architecture

The change is confined to **`src/bellwether/connectors/x.py`** — nothing else.

### 1. Request the entities

Change the fetch URL's fields from `tweet.fields=created_at` to
`tweet.fields=created_at,entities`. Same endpoint, same call, same API tier — X returns the
unwound link cards (`entities.urls[]` with `title`, `description`, `expanded_url`,
`unwound_url`) inline at no extra cost.

### 2. Fold each card into the statement text

In `_parse_x_timeline`, build each `RawItem.text` as the tweet's own `text` followed by one
paragraph per URL entity that has a title and/or description:

```
<original tweet text>

<title> — <description>
```

Rules:
- Use `"<title> — <description>"` when both are present; just the one that exists otherwise.
- **Skip** a URL entity that has neither title nor description (plain links X did not unwind).
- Multiple URL cards → one paragraph each, in entity order.
- Keep the original tweet text unchanged (including the raw `t.co` URL); cards are appended
  after a blank-line separator.
- If the tweet has no `entities`/`urls`, `text` is the original tweet text (behavior
  unchanged from today).

**Example** (`https://t.co/mxgaKQUZo1` → an X broadcast):
```
https://t.co/mxgaKQUZo1

The White House — President Trump Delivers Remarks at the Salute to America Celebration
```

## Consistency with the anti-fabrication guard

The extract stage requires each `evidence_quote` to be a **verbatim substring** of the
statement text (`is_verbatim`). Because the card title/description now *become* part of the
stored statement text, an extraction can legitimately quote them. The enrichment adds real
stored text — it does not fabricate — so it is fully consistent with that invariant.

## Data flow

```
XConnector.fetch()  (query=from:handle, tweet.fields=created_at,entities)
  -> _parse_x_timeline: RawItem.text = tweet.text + folded card title/description
  -> ingest -> statements (status="new") -> detect/extract  (now sees the card content)
```

## Error handling

- Missing `entities` or `urls` on a tweet → no cards appended (original text). No error.
- A URL entity missing both `title` and `description` → skipped.
- `XConnector.fetch` keeps its existing behavior: no `api_key` → `[]`; a request exception →
  `[]` (unchanged). Requesting `entities` does not change any of these paths.

## Testing

- **Unit** — `_parse_x_timeline(payload, handle)` with crafted payloads (no network):
  - a tweet whose URL entity has `title` + `description` → `RawItem.text` contains
    `"<title> — <description>"` after the tweet text.
  - a tweet with no `entities` key → `text` equals the raw tweet text (unchanged).
  - a URL entity with neither `title` nor `description` → nothing appended.
  - a tweet with two URL cards → both card paragraphs present.
- **Live** (before merge): fetch `@realDonaldTrump`; confirm the link-only tweet's ingested
  text now contains "The White House — President Trump Delivers Remarks…".

## Out of scope (YAGNI)

- No external page fetch / link resolution — X returns the card text directly.
- No "thin tweet" heuristic — cards are always folded in.
- No stripping of the `t.co` URL from the text.
- No dedup of repeated cards.
- No change to the news/RSS connectors, the pipeline, the schema, or the frontend.
