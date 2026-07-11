# Signal Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user page through all signals with offset-based Prev/Next controls (page size 25).

**Architecture:** Add an `offset` query param to `GET /signals` (already ordered `id DESC`). The dashboard's "Recent signals" panel tracks an `offset` and steps through pages with Prev/Next, requesting `limit=25&offset=<offset>`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, pytest (real Postgres); Next.js/React, SWR, vitest + @testing-library/react.

## Global Constraints

- Backend: one new param `offset: int = Query(default=0, ge=0)` on `/signals`, applied as `.order_by(Extraction.id.desc()).offset(offset).limit(limit)`. Everything else (owner scoping, `figure_id`/`direction`/`min_confidence` filters, `SignalRead` shape, `limit=Query(default=50, ge=1, le=500)`) unchanged.
- Frontend page size **25** (`const PAGE = 25`). Requests `/signals?limit=25&offset=<offset>` (+ `direction` when set). SWR key includes `offset` and `direction`.
- **Next** disabled when the current page returned **fewer than `PAGE`** items; **Prev** disabled when `offset === 0`; a **"Page N"** indicator (`N = offset / PAGE + 1`); changing **direction resets `offset` to 0**. Each Next/Prev **replaces** the list (stepped paging, not append/infinite-scroll).
- Real-Postgres API tests (`client`/`auth_headers`); vitest for frontend. Don't modify `tests/conftest.py`/`tests/api/conftest.py`.
- **Test-env note:** the backend suite shares the dev Postgres; the running dev app (`./scripts/dev.sh`) must be stopped before running pytest, or the `db_session` fixture's table-clear collides with live app writes.
- No total-count / jump-to-page, no infinite scroll, no cursor, no change to Impacts or the live-alerts panel.

---

### Task 1: Backend — `offset` param on `/signals`

**Files:**
- Modify: `src/bellwether/api/feed.py` (`signals` handler)
- Test: `tests/api/test_feed_api.py` (add a test)

**Interfaces:**
- Produces: `GET /signals?limit=<n>&offset=<m>` returns the `m`-th page of `SignalRead[]`, ordered `Extraction.id DESC`.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_feed_api.py` (the file already imports `datetime, timezone`, `select`, `User`, `Figure`, `Source`, `Statement`, `Extraction`):

```python
def test_signals_offset_pagination(client, auth_headers, db_session):
    tester = db_session.execute(select(User).where(User.username == "tester")).scalar_one()
    f = Figure(name="P", type="individual", aliases=[], owner_id=tester.id)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="news", config={"query": "P"},
               provenance="news", origin="auto", owner_id=tester.id)
    db_session.add(s); db_session.flush()
    ex_ids = []
    for i in range(5):
        st = Statement(figure_id=f.id, source_id=s.id, external_id=f"e{i}", text=f"t{i}", url=None,
                       provenance="news", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                       status="resolved")
        db_session.add(st); db_session.flush()
        ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="small",
                        confidence=0.5, evidence_quote=f"t{i}", model="m", version="baseline")
        db_session.add(ex); db_session.flush()
        ex_ids.append(ex.id)

    # newest first (id DESC): ex_ids[4], ex_ids[3], ...
    page1 = client.get("/signals?limit=2&offset=0", headers=auth_headers).json()
    page2 = client.get("/signals?limit=2&offset=2", headers=auth_headers).json()
    assert [x["id"] for x in page1] == [ex_ids[4], ex_ids[3]]
    assert [x["id"] for x in page2] == [ex_ids[2], ex_ids[1]]
    assert set(x["id"] for x in page1).isdisjoint(x["id"] for x in page2)   # no overlap
    # offset past the end -> empty page
    assert client.get("/signals?limit=2&offset=10", headers=auth_headers).json() == []
    # offset composes with the direction filter
    upp = client.get("/signals?direction=up&limit=2&offset=2", headers=auth_headers).json()
    assert [x["id"] for x in upp] == [ex_ids[2], ex_ids[1]]
    # negative offset rejected
    assert client.get("/signals?offset=-1", headers=auth_headers).status_code == 422
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py::test_signals_offset_pagination -q`
Expected: FAIL — `page2` equals `page1` (no `offset` support yet, so both return the newest 2), so the `page2 == [ex_ids[2], ex_ids[1]]` assertion fails.

- [ ] **Step 3: Add the `offset` param**

In `src/bellwether/api/feed.py`, change the `signals` signature from:

```python
def signals(figure_id: int | None = None, direction: str | None = None,
            min_confidence: float | None = None, limit: int = Query(default=50, ge=1, le=500),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
```

to (add the `offset` param):

```python
def signals(figure_id: int | None = None, direction: str | None = None,
            min_confidence: float | None = None, limit: int = Query(default=50, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
```

and change the ordering/limit line from:

```python
    q = q.order_by(Extraction.id.desc()).limit(limit)
```

to:

```python
    q = q.order_by(Extraction.id.desc()).offset(offset).limit(limit)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py::test_signals_offset_pagination -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the feed test file for regressions**

Run: `.venv/bin/python -m pytest tests/api/test_feed_api.py -q`
Expected: PASS (all — auth, owner-isolation, source-context, and the new pagination test).

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/api/feed.py tests/api/test_feed_api.py
git commit -m "feat: offset pagination on /signals"
```

---

### Task 2: Frontend — Prev/Next paging in the feed

**Files:**
- Modify: `frontend/src/app/(dash)/page.tsx`
- Test: `frontend/src/app/(dash)/page.test.tsx` (create)

**Interfaces:**
- Consumes: `GET /signals?limit=25&offset=<n>` from Task 1.
- Produces: the "Recent signals" panel pages with Prev/Next.

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/app/(dash)/page.test.tsx`:

```tsx
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";

const GET = vi.fn();
vi.mock("@/api/client", () => ({ client: { GET: (...a: any[]) => GET(...a) } }));
vi.mock("@/hooks/useAlertStream", () => ({ useAlertStream: () => ({ alerts: [], connected: false }) }));

import FeedPage from "./page";

function makeSignals(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1, direction: "up", magnitude: "small", confidence: 0.5, entities: [],
    text: `sig ${i + 1}`, url: null, source_type: "news", figure_name: "F",
    published_at: "2026-07-11T00:00:00Z", evidence_quote: "q",
  }));
}

function renderFeed() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <FeedPage />
    </SWRConfig>,
  );
}

function lastQuery() {
  return GET.mock.calls[GET.mock.calls.length - 1][1].params.query;
}

beforeEach(() => { GET.mockReset(); });

describe("FeedPage pagination", () => {
  it("requests limit=25 offset=0 initially and disables Prev", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(GET).toHaveBeenCalled());
    expect(lastQuery().limit).toBe(25);
    expect(lastQuery().offset).toBe(0);
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
  });

  it("Next advances the offset by 25", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(lastQuery().offset).toBe(25));
  });

  it("disables Next when a page returns fewer than 25", async () => {
    GET.mockResolvedValue({ data: makeSignals(10) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("changing direction resets to page 1 (offset 0)", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /next/i }));   // -> offset 25
    await userEvent.selectOptions(screen.getByRole("combobox"), "down");    // reset
    await waitFor(() => {
      expect(lastQuery().offset).toBe(0);
      expect(lastQuery().direction).toBe("down");
    });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix frontend test -- "src/app/(dash)/page.test.tsx"`
Expected: FAIL — `lastQuery().offset` is `undefined` (the page doesn't send `offset` yet) and there are no Prev/Next buttons, so `getByRole("button", { name: /prev/i })` throws.

- [ ] **Step 3: Add paging to the feed page**

Edit `frontend/src/app/(dash)/page.tsx`. Add a module constant above the component (after the imports, before `export default function FeedPage()`):

```tsx
const PAGE = 25;
```

Change the state + SWR block from:

```tsx
  const { alerts, connected } = useAlertStream();
  const [direction, setDirection] = useState("");
  const { data: signals, isLoading, error } = useSWR(["/signals", direction], async () => {
    const { data } = await client.GET("/signals", { params: { query: direction ? { direction } : {} } });
    return data ?? [];
  });
```

to:

```tsx
  const { alerts, connected } = useAlertStream();
  const [direction, setDirection] = useState("");
  const [offset, setOffset] = useState(0);
  const { data: signals, isLoading, error } = useSWR(["/signals", direction, offset], async () => {
    const query = { limit: PAGE, offset, ...(direction ? { direction } : {}) };
    const { data } = await client.GET("/signals", { params: { query: query as any } });
    return data ?? [];
  });
```

Change the direction `<select>`'s `onChange` from:

```tsx
        <select className="mb-2 rounded border p-1 text-sm" value={direction} onChange={(e) => setDirection(e.target.value)}>
```

to (also reset the offset):

```tsx
        <select className="mb-2 rounded border p-1 text-sm" value={direction}
                onChange={(e) => { setDirection(e.target.value); setOffset(0); }}>
```

And insert the Prev/Next controls immediately after the signals `</ul>` (i.e. right before the section's closing `</section>`):

```tsx
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-40"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</button>
          <span className="text-gray-500">Page {offset / PAGE + 1}</span>
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-40"
                  disabled={(signals?.length ?? 0) < PAGE}
                  onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm --prefix frontend test -- "src/app/(dash)/page.test.tsx"`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full frontend suite for regressions**

Run: `npm --prefix frontend test`
Expected: PASS (all — the 4 new tests plus the existing ones).

- [ ] **Step 6: Commit**

```bash
git add "frontend/src/app/(dash)/page.tsx" "frontend/src/app/(dash)/page.test.tsx"
git commit -m "feat(ui): Prev/Next pagination for the signals feed"
```

---

## Live verification (before merge, not a task)

With the dev stack running (`./scripts/dev.sh`) and > 25 signals present (the Trump figure has 100+ news signals), open http://localhost:3000: the "Recent signals" panel shows Page 1 with Prev disabled; **Next** loads Page 2 (older signals); **Prev** returns; changing the direction filter snaps back to Page 1. Optionally confirm the request:
`curl -s "localhost:8000/signals?limit=25&offset=25" -H "Authorization: Bearer <token>" | python -m json.tool | head` returns a distinct page from `offset=0`.

## Self-review notes

- **Spec coverage:** `offset` param + `.offset()` (Task 1) ✓; unchanged filters/scoping/limit (Task 1 Step 3) ✓; PAGE=25 + `limit`/`offset` request (Task 2 Step 3) ✓; Next-disabled-on-short-page, Prev-disabled-at-0, "Page N", direction-resets-offset (Task 2 Step 3 + tests) ✓; backend + frontend tests ✓; live verification ✓; accepted-drift trade-off is inherent to the offset approach (no code) ✓.
- **Type consistency:** the request query keys (`limit`, `offset`, `direction`) match the backend param names exactly; `PAGE` is defined once and used in the fetcher, the "Page N" math, and both button predicates.
- **Placeholder scan:** none — every step has concrete code/commands.
- `query as any` casts around the openapi-fetch typed query because the generated `schema.ts` isn't regenerated for the new `offset` param (consistent with the file's existing `any` usage); no schema regen required.
