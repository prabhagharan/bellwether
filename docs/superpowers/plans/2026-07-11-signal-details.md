# Enriched Signal Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show source context (headline, link, source type, figure, date, evidence quote) on each signal, as a compact card that expands on click.

**Architecture:** Widen `SignalRead` and the `/signals` query (which already joins Statement + Figure) with one more join to Source, returning the richer shape. The frontend renders each signal via a `SignalItem` component with its own expand state.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, pytest (real Postgres); Next.js/React, vitest + @testing-library/react.

## Global Constraints

- **New `SignalRead` fields** (added to the existing `id, statement_id, direction, magnitude, confidence, entities, version`): `text` (`Statement.text`), `url` (`Statement.url`, `str | None`), `source_type` (`Source.connector_type`), `figure_name` (`Figure.name`), `published_at` (`Statement.published_at`, `datetime`), `evidence_quote` (`Extraction.evidence_quote`).
- **Inner join to `Source`** — `Statement.source_id` is `NOT NULL` + `ON DELETE CASCADE`, so every statement has a live source; `source_type` is always present. No outer join, no null-source case.
- Same query params/filters (`figure_id`, `direction`, `min_confidence`), ordering (`Extraction.id desc`), and `limit` as today.
- **Frontend:** compact card by default (badge + truncated headline + confidence + caret); expands on click to show meta line (`source_type · figure_name · date`), a `↗` link to `url` (new tab; omitted when null), the evidence quote (muted italic), and entities. Direction badge color: `up` green, `down` red, `neutral` gray. Degrade gracefully on null `url`.
- Real-Postgres API tests via `client`/`auth_headers`; frontend tests via vitest. Don't modify `tests/conftest.py` or `tests/api/conftest.py`.
- No pagination, no new filters, no change to the Impacts page or the live-alerts panel.

---

### Task 1: Backend — enrich `SignalRead` + `/signals`

**Files:**
- Modify: `src/bellwether/api/schemas.py` (add fields to `SignalRead`, ~line 133)
- Modify: `src/bellwether/api/feed.py` (import `Source`; join it; build enriched `SignalRead`)
- Test: `tests/api/test_feed_api.py` (add a test)

**Interfaces:**
- Produces: `GET /signals` returns `SignalRead[]` where each item now also has `text: str`, `url: str | None`, `source_type: str`, `figure_name: str`, `published_at: datetime`, `evidence_quote: str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_feed_api.py`. First extend the imports at the top of the file — change:

```python
from datetime import datetime, timezone
from bellwether.models.user import User
```

to:

```python
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.user import User
```

Then append this test:

```python
def test_signals_include_source_context(client, auth_headers, db_session):
    """/signals carries the underlying source context: headline, url, source type,
    figure name, published date, and the evidence quote."""
    tester = db_session.execute(select(User).where(User.username == "tester")).scalar_one()
    f = Figure(name="Donald Trump", type="individual", aliases=[], owner_id=tester.id)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="news", config={"query": "Donald Trump"},
               provenance="news", origin="auto", owner_id=tester.id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e1",
                   text="Trump signals 25% EV tariffs", url="https://news.example/article",
                   provenance="news", published_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
                   status="resolved")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="down", magnitude="moderate",
                    confidence=0.72, evidence_quote="tariffs will crush margins",
                    model="m", version="baseline")
    db_session.add(ex); db_session.flush()

    rows = client.get("/signals", headers=auth_headers).json()
    row = next(x for x in rows if x["id"] == ex.id)
    assert row["text"] == "Trump signals 25% EV tariffs"
    assert row["url"] == "https://news.example/article"
    assert row["source_type"] == "news"
    assert row["figure_name"] == "Donald Trump"
    assert row["published_at"].startswith("2026-07-11")
    assert row["evidence_quote"] == "tariffs will crush margins"
    # existing fields still present
    assert row["direction"] == "down" and row["magnitude"] == "moderate"
    assert row["entities"] == ["TSLA"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py::test_signals_include_source_context -q`
Expected: FAIL — `KeyError: 'text'` (the response has no `text` field yet).

- [ ] **Step 3: Add the fields to `SignalRead`**

In `src/bellwether/api/schemas.py`, change the `SignalRead` class from:

```python
class SignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    statement_id: int
    direction: str
    magnitude: str
    confidence: float
    entities: list
    version: str
```

to:

```python
class SignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    statement_id: int
    direction: str
    magnitude: str
    confidence: float
    entities: list
    version: str
    text: str
    url: str | None
    source_type: str
    figure_name: str
    published_at: datetime
    evidence_quote: str
```

(`datetime` is already imported at the top of `schemas.py`.)

- [ ] **Step 4: Join Source and build the enriched response**

In `src/bellwether/api/feed.py`, add the `Source` import with the other model imports:

```python
from bellwether.models.source import Source
```

Then change the `signals` handler body from:

```python
    q = (select(Extraction).join(Statement, Statement.id == Extraction.statement_id)
         .join(Figure, Figure.id == Statement.figure_id).where(Figure.owner_id == user.id))
    if figure_id is not None:
        q = q.where(Statement.figure_id == figure_id)
    if direction is not None:
        q = q.where(Extraction.direction == direction)
    if min_confidence is not None:
        q = q.where(Extraction.confidence >= min_confidence)
    q = q.order_by(Extraction.id.desc()).limit(limit)
    return list(session.execute(q).scalars())
```

to:

```python
    q = (select(Extraction, Statement.text, Statement.url, Statement.published_at,
                Source.connector_type, Figure.name)
         .join(Statement, Statement.id == Extraction.statement_id)
         .join(Source, Source.id == Statement.source_id)
         .join(Figure, Figure.id == Statement.figure_id)
         .where(Figure.owner_id == user.id))
    if figure_id is not None:
        q = q.where(Statement.figure_id == figure_id)
    if direction is not None:
        q = q.where(Extraction.direction == direction)
    if min_confidence is not None:
        q = q.where(Extraction.confidence >= min_confidence)
    q = q.order_by(Extraction.id.desc()).limit(limit)
    return [
        SignalRead(
            id=ex.id, statement_id=ex.statement_id, direction=ex.direction,
            magnitude=ex.magnitude, confidence=ex.confidence, entities=ex.entities,
            version=ex.version, text=text, url=url, source_type=connector_type,
            figure_name=figure_name, published_at=published_at, evidence_quote=ex.evidence_quote,
        )
        for ex, text, url, published_at, connector_type, figure_name in session.execute(q).all()
    ]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py::test_signals_include_source_context -q`
Expected: PASS (1 passed)

- [ ] **Step 6: Run the full feed test file for regressions**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py -q`
Expected: PASS (all — the owner-isolation and auth tests still hold; the inner join to Source doesn't change which owners' rows are returned).

- [ ] **Step 7: Commit**

```bash
git add src/bellwether/api/schemas.py src/bellwether/api/feed.py tests/api/test_feed_api.py
git commit -m "feat: enrich /signals with source context (headline, url, source_type, figure, date, quote)"
```

---

### Task 2: Frontend — compact, expandable `SignalItem` card

**Files:**
- Create: `frontend/src/components/SignalItem.tsx`
- Modify: `frontend/src/app/(dash)/page.tsx` (import + use `SignalItem` in "Recent signals")
- Test: `frontend/src/components/SignalItem.test.tsx` (create)

**Interfaces:**
- Consumes: a signal object with the Task 1 fields (`direction, magnitude, confidence, entities, text, url, source_type, figure_name, published_at, evidence_quote`).
- Produces: `SignalItem` component (`export function SignalItem({ signal }: { signal: any })`).

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/components/SignalItem.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SignalItem } from "./SignalItem";

const signal = {
  id: 1, direction: "down", magnitude: "moderate", confidence: 0.72, entities: ["TSLA"],
  text: "Trump signals 25% EV tariffs\n\nfull body", url: "https://news.example/x",
  source_type: "news", figure_name: "Donald Trump",
  published_at: "2026-07-11T00:00:00Z", evidence_quote: "tariffs will crush margins",
};

describe("SignalItem", () => {
  it("shows a compact summary and hides details until expanded", () => {
    render(<SignalItem signal={signal} />);
    expect(screen.getByText(/Trump signals 25% EV tariffs/)).toBeTruthy();
    expect(screen.getByText("down/moderate")).toBeTruthy();
    expect(screen.queryByText(/tariffs will crush margins/)).toBeNull();
  });

  it("expands on click to reveal source line, link, and evidence quote", () => {
    render(<SignalItem signal={signal} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/tariffs will crush margins/)).toBeTruthy();
    expect(screen.getByText(/news · Donald Trump/)).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://news.example/x");
  });

  it("renders no link when url is null", () => {
    render(<SignalItem signal={{ ...signal, url: null }} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("link")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix frontend test -- src/components/SignalItem.test.tsx`
Expected: FAIL — cannot resolve `./SignalItem` (module doesn't exist yet).

- [ ] **Step 3: Create the `SignalItem` component**

Create `frontend/src/components/SignalItem.tsx`:

```tsx
"use client";
import { useState } from "react";

const TONE: Record<string, string> = {
  up: "bg-green-100 text-green-800",
  down: "bg-red-100 text-red-800",
  neutral: "bg-gray-100 text-gray-700",
};

export function SignalItem({ signal }: { signal: any }) {
  const [open, setOpen] = useState(false);
  const headline = (signal.text ?? "").split("\n")[0] || "(no text)";
  const date = signal.published_at ? new Date(signal.published_at).toLocaleDateString() : "";

  return (
    <li className="rounded border bg-white text-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 p-3 text-left"
      >
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${TONE[signal.direction] ?? TONE.neutral}`}>
          {signal.direction}/{signal.magnitude}
        </span>
        <span className="flex-1 truncate">{headline}</span>
        <span className="shrink-0 text-gray-400">conf {signal.confidence?.toFixed?.(2)}</span>
      </button>
      {open && (
        <div className="space-y-1 border-t px-3 pb-3 pt-2">
          <div className="text-xs text-gray-500">
            {signal.source_type} · {signal.figure_name}{date ? ` · ${date}` : ""}
          </div>
          {signal.url && (
            <a href={signal.url} target="_blank" rel="noreferrer"
               className="block text-blue-600 hover:underline">
              {headline} ↗
            </a>
          )}
          {signal.evidence_quote && (
            <p className="italic text-gray-500">“{signal.evidence_quote}”</p>
          )}
          {(signal.entities ?? []).length > 0 && (
            <div className="text-xs text-gray-500">{(signal.entities ?? []).join(", ")}</div>
          )}
        </div>
      )}
    </li>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix frontend test -- src/components/SignalItem.test.tsx`
Expected: PASS (3 passed)

- [ ] **Step 5: Use `SignalItem` in the feed page**

In `frontend/src/app/(dash)/page.tsx`, add the import near the top (with the other imports):

```tsx
import { SignalItem } from "@/components/SignalItem";
```

Then change the "Recent signals" list from:

```tsx
        <ul className="space-y-2">
          {(signals ?? []).map((s: any) => (
            <li key={s.id} className="rounded border bg-white p-3 text-sm">
              {s.direction}/{s.magnitude} · conf {s.confidence?.toFixed?.(2)} · {(s.entities ?? []).join(", ")}
            </li>
          ))}
        </ul>
```

to:

```tsx
        <ul className="space-y-2">
          {(signals ?? []).map((s: any) => (
            <SignalItem key={s.id} signal={s} />
          ))}
        </ul>
```

- [ ] **Step 6: Run the frontend test suite for regressions**

Run: `npm --prefix frontend test`
Expected: PASS (all — the new SignalItem tests plus the existing ones).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/SignalItem.tsx frontend/src/components/SignalItem.test.tsx "frontend/src/app/(dash)/page.tsx"
git commit -m "feat(ui): compact, expandable signal cards with source context"
```

---

## Live verification (before merge, not a task)

With the dev stack running (`./scripts/dev.sh`) and signals present:

1. `curl -s localhost:8000/signals -H "Authorization: Bearer <token>" | python -m json.tool | head` — confirm each signal carries `text`, `url`, `source_type`, `figure_name`, `published_at`, `evidence_quote`.
2. Open http://localhost:3000, "Recent signals" — each card is a compact one-liner; click one and confirm it expands to show the source line, the `↗` link (opens the article), and the evidence quote.

## Self-review notes

- **Spec coverage:** SignalRead fields (Task 1 Step 3) ✓; inner join to Source, always-present source_type (Task 1 Step 4 + Global Constraints) ✓; unchanged params/filters/order/limit (Task 1 Step 4) ✓; compact-expand card with badge/headline/conf → meta+link+quote+entities (Task 2 Step 3) ✓; direction colors, null-url graceful (Task 2 Step 3 + test) ✓; backend + frontend tests ✓; live verification ✓.
- **Type consistency:** the six new field names (`text, url, source_type, figure_name, published_at, evidence_quote`) are identical across `SignalRead` (Task 1), the `/signals` builder (Task 1), the API test (Task 1), the `SignalItem` component (Task 2), and its test (Task 2).
- **Placeholder scan:** none — every step has concrete code/commands.
- The `/signals` builder destructures `for ex, text, url, published_at, connector_type, figure_name in ...` in the exact column order of the `select(...)`.
