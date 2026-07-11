# Signal Pagination — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

`GET /signals` returns only the newest `limit` (default 50) extractions, ordered
`Extraction.id DESC`, with no way to page further back. The dashboard's "Recent signals"
panel fetches once and renders that single page — older signals are unreachable.

## Goal

Let the user page through all their signals with offset-based Prev/Next controls.

## Decisions (locked)

1. **Offset pagination** (numbered/stepped pages) — an `offset` query param on `/signals`.
2. **Prev/Next stepping** (not jump-to-arbitrary-page) — keeps the `/signals` response a plain
   `SignalRead[]`; no total-count query, no response reshape.
3. **UI page size = 25** (via the existing `limit` param).

## Architecture

### 1. Backend — `offset` on `/signals`

`src/bellwether/api/feed.py::signals` gains one param:

```python
offset: int = Query(default=0, ge=0)
```

applied to the existing query after ordering:

```python
q = q.order_by(Extraction.id.desc()).offset(offset).limit(limit)
```

Everything else is unchanged: owner scoping (`Figure.owner_id == user.id`), the
`figure_id`/`direction`/`min_confidence` filters, the enriched `SignalRead` shape, and the
`limit` param (`default=50, ge=1, le=500`). The frontend passes `limit=25`.

### 2. Frontend — Prev/Next controls (`frontend/src/app/(dash)/page.tsx`)

The "Recent signals" panel gains offset state and stepping controls:

- `const PAGE = 25;` and `const [offset, setOffset] = useState(0);`
- The SWR fetch key includes `offset` and `direction`; it calls
  `/signals?limit=25&offset=<offset>` (plus `direction` when set).
- **Next** → `setOffset(offset + PAGE)`. Disabled when the current page returned **fewer than
  `PAGE`** items (last page).
- **Prev** → `setOffset(Math.max(0, offset - PAGE))`. Disabled when `offset === 0`.
- A **"Page N"** indicator where `N = offset / PAGE + 1`.
- Changing the **direction filter resets `offset` to 0** (otherwise a filtered result set could
  land on an empty page).

Each Next/Prev **replaces** the visible list with that page (not append) — this is stepped
paging, not infinite scroll.

## Data flow

```
"Recent signals" (offset state, PAGE=25)
  GET /signals?limit=25&offset=<offset>[&direction=<dir>]
    -> SignalRead[] (<=25)  ordered id DESC, owner-scoped
  Next -> offset += 25 ; Prev -> offset -= 25 ; direction change -> offset = 0
  Next disabled when page length < 25 ; Prev disabled when offset == 0
```

## Accepted trade-off

On a live `id DESC` feed, new signals arriving at the top shift everything down, so between
page loads a signal may appear twice or be skipped at a page boundary. This is inherent to
offset paging and was accepted when choosing offset over a cursor.

## Error handling

- `offset` past the end of the result set → `[]` (empty page). The frontend shows an empty
  list; Next is disabled (short page), Prev returns to real data.
- `offset < 0` rejected by `Query(ge=0)` (422), consistent with the existing `limit` bounds.

## Testing

- **Backend** (`tests/api/test_feed_api.py`): seed more than one page of owner-owned signals;
  assert `offset=0&limit=N` returns the newest N and `offset=N&limit=N` returns the *next* N
  with a **disjoint** id set in correct `id DESC` order; `offset` past the end → `[]`;
  `offset` composes with a `direction` filter (paging within the filtered set).
- **Frontend** (`page.tsx` component test, vitest): Next advances the offset and requests the
  next page; Prev goes back; Prev is disabled on page 1; Next is disabled when a page returns
  fewer than `PAGE` items; changing the direction filter resets to page 1.

## Out of scope (YAGNI)

- No total count / jump-to-arbitrary-page ("Page 2 of 9") — Prev/Next only.
- No infinite scroll, no cursor/`before_id`.
- No pagination on the Impacts page or the live-alerts (SSE) panel.
- No change to the `SignalRead` shape or the other `/signals` filters.
