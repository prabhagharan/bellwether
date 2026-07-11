# Ingestion Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ingest` worker stage that polls each enabled source on its own `poll_interval_seconds` cadence, creating `new` statements for the existing pipeline.

**Architecture:** Mirror the existing impact due-queue. A new `queue.claim_due_source` claims one enabled source whose `last_polled_at + poll_interval_seconds` has elapsed, using `FOR UPDATE SKIP LOCKED`, and **stamps `last_polled_at = now()` and commits before fetching** ("stamp-first"). This makes `last_polled_at` serve as both the schedule clock and the claim guard, so there is no new column, no migration, and `reclaim` is a no-op. A new `make_ingest_stage` wraps the existing `ingest_source`, wired into `_build_stage` and the worker CLI.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (`with_for_update(skip_locked=True)`, `func.make_interval`), Postgres, pytest (real Postgres via the `db_session` fixture).

## Global Constraints

- **Stamp-first claim:** the claim stamps `last_polled_at = now()` and commits BEFORE the fetch. No new columns, **no Alembic migration**, `reclaim` is `lambda s, secs: 0`. (Spec §Architecture.)
- **Failure = back off one interval:** a failed fetch is not retried until the next `poll_interval_seconds`; this is achieved for free because the timer was advanced at claim time. Errors are logged by `run_worker`'s existing try/except. (Spec §Decisions.)
- **Mirror `queue.claim_due_impact`** exactly for lock discipline: `select(...).where(...).order_by(...).with_for_update(skip_locked=True).limit(1)`, then stamp + `session.commit()`. (Spec §Architecture.)
- **One source per tick** (like every other stage claims one row per loop iteration). (Spec §Scheduler granularity.)
- **`ingest_source` in `src/bellwether/ingest.py` is unchanged.** (Spec §Components.)
- **Pipeline is now 7 stages** (was 6): `detect, extract, resolve, measure, discovery, alert, ingest`. (Spec §Deployment & docs.)
- **Tests use real Postgres** via the `db_session` fixture (`join_transaction_mode="create_savepoint"` — `session.commit()` inside a test is savepoint-isolated). Do **not** modify `tests/conftest.py`. Use `owner_id=None` when seeding. (AGENTS.md.)
- **SKIP-LOCKED concurrency is inherited verbatim from `claim_due_impact` and is not separately unit-tested**, matching the existing queue tests (`test_queue_discovery.py` tests claim/reclaim logic on one session, never two-connection locking). Do not add a flaky two-connection lock test.
- **Live verification before merge:** external RSS/X connectors are stubbed in the suite; run one real feed poll (`python -m bellwether.worker ingest --once`) against a live source before merge. (AGENTS.md — stubs hide integration bugs.)

---

### Task 1: `claim_due_source` in `queue.py`

**Files:**
- Modify: `src/bellwether/queue.py` (add import + new function; mirror `claim_due_impact`)
- Test: `tests/test_queue_ingest.py` (create)

**Interfaces:**
- Consumes: `bellwether.models.source.Source` (fields `enabled: bool`, `poll_interval_seconds: int`, `last_polled_at: datetime | None`).
- Produces: `claim_due_source(session: Session) -> Source | None` — claims one due enabled source, stamps `last_polled_at = now()`, commits, and returns it; returns `None` when no source is due.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_queue_ingest.py`:

```python
from datetime import datetime, timedelta, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.queue import claim_due_source


def _source(db_session, *, enabled=True, last_polled=None, interval=300):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": "http://x/feed"},
               provenance="primary", origin="manual", owner_id=None,
               enabled=enabled, poll_interval_seconds=interval, last_polled_at=last_polled)
    db_session.add(s); db_session.flush()
    return s


def test_never_polled_source_is_due(db_session):
    s = _source(db_session, last_polled=None)
    claimed = claim_due_source(db_session)
    assert claimed is not None and claimed.id == s.id
    assert claimed.last_polled_at is not None            # timer stamped at claim


def test_recently_polled_source_not_due(db_session):
    _source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=10), interval=300)
    assert claim_due_source(db_session) is None


def test_past_interval_source_is_due_again(db_session):
    s = _source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=400), interval=300)
    claimed = claim_due_source(db_session)
    assert claimed is not None and claimed.id == s.id


def test_disabled_source_never_claimed(db_session):
    _source(db_session, enabled=False, last_polled=None)
    assert claim_due_source(db_session) is None


def test_claim_advances_the_timer(db_session):
    s = _source(db_session, last_polled=None)
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    claim_due_source(db_session)
    db_session.refresh(s)
    assert s.last_polled_at >= before                    # advanced to ~now, so no longer due
    assert claim_due_source(db_session) is None          # not due immediately after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_queue_ingest.py -q`
Expected: FAIL with `ImportError: cannot import name 'claim_due_source' from 'bellwether.queue'`

- [ ] **Step 3: Implement `claim_due_source`**

In `src/bellwether/queue.py`, extend the existing import line and add the function.

Change the top imports from:

```python
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.impact import Impact
from bellwether.models.figure import Figure
from bellwether.models.extraction import Extraction
```

to (add `func, or_` and the `Source` model):

```python
from sqlalchemy import select, update, func, or_
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.impact import Impact
from bellwether.models.figure import Figure
from bellwether.models.extraction import Extraction
from bellwether.models.source import Source
```

Then add this function (mirrors `claim_due_impact`'s lock-then-stamp discipline):

```python
def claim_due_source(session: Session) -> Source | None:
    """Claim one enabled source whose poll interval has elapsed, stamp-first.

    Mirrors claim_due_impact, but the schedule clock IS the claim guard: we stamp
    last_polled_at = now() and commit BEFORE the caller fetches. A source is "due"
    when it has never been polled, or last_polled_at + poll_interval_seconds <= now.
    Because the timer is advanced at claim time, a failed fetch simply backs off a
    full interval, and a crash mid-fetch strands nothing (no in-flight status), so
    there is no reclaim for this stage.
    """
    now = datetime.now(timezone.utc)
    due_at = Source.last_polled_at + func.make_interval(0, 0, 0, 0, 0, 0, Source.poll_interval_seconds)
    source = session.execute(
        select(Source)
        .where(
            Source.enabled.is_(True),
            or_(Source.last_polled_at.is_(None), due_at <= now),
        )
        .order_by(Source.last_polled_at.asc().nullsfirst())
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if source is None:
        return None
    source.last_polled_at = now
    session.commit()
    return source
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_queue_ingest.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/queue.py tests/test_queue_ingest.py
git commit -m "feat: claim_due_source — stamp-first due-source claim for ingestion"
```

---

### Task 2: `ingest` worker stage + CLI wiring

**Files:**
- Modify: `src/bellwether/worker.py` (add import of `ingest_source` and `claim_due_source`; add `make_ingest_stage`; handle `"ingest"` in `_build_stage`; add `"ingest"` to CLI `choices`)
- Test: `tests/test_worker_ingest.py` (create)

**Interfaces:**
- Consumes: `claim_due_source(session) -> Source | None` (Task 1); `bellwether.ingest.ingest_source(session, source) -> list[Statement]` (existing — fetches via `build_connector`, dedups by `external_id`, inserts `status="new"` statements, does NOT commit).
- Produces: `make_ingest_stage() -> Stage` with `name="ingest"`; `python -m bellwether.worker ingest` runnable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worker_ingest.py`. Reuses the RSS feed fixture that `tests/test_ingest.py` uses.

```python
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.worker import make_ingest_stage, main
import bellwether.ingest as ingest_mod

FEED = str(Path(__file__).parent / "fixtures" / "sample_feed.xml")


def _rss_source(db_session, *, enabled=True, last_polled=None, interval=300, feed=FEED):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": feed},
               provenance="primary", origin="manual", owner_id=None,
               enabled=enabled, poll_interval_seconds=interval, last_polled_at=last_polled)
    db_session.add(s); db_session.flush()
    return s


def test_ingest_stage_polls_due_source(db_session):
    s = _rss_source(db_session, last_polled=None)
    stage = make_ingest_stage()
    row = stage.claim_next(db_session)
    assert row is not None and row.id == s.id
    stage.process(db_session, row)
    stmts = db_session.execute(select(Statement).where(Statement.source_id == s.id)).scalars().all()
    assert len(stmts) == 2 and all(st.status == "new" for st in stmts)


def test_ingest_stage_skips_not_due_source(db_session):
    _rss_source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=5), interval=300)
    stage = make_ingest_stage()
    assert stage.claim_next(db_session) is None


def test_ingest_stage_backs_off_on_fetch_error(db_session, monkeypatch):
    class Boom:
        def fetch(self):
            raise RuntimeError("feed 500")
    monkeypatch.setattr(ingest_mod, "build_connector", lambda source: Boom())
    s = _rss_source(db_session, last_polled=None)
    stage = make_ingest_stage()
    row = stage.claim_next(db_session)                 # claim stamps + commits last_polled_at
    polled_after_claim = row.last_polled_at
    assert polled_after_claim is not None
    with pytest.raises(RuntimeError):                  # run_worker's loop would catch+log this
        stage.process(db_session, row)
    db_session.rollback()                              # mimic run_worker rolling back the failed process
    db_session.refresh(s)
    n = db_session.execute(select(func.count()).select_from(Statement).where(Statement.source_id == s.id)).scalar_one()
    assert n == 0                                      # no statements from the failed fetch
    assert s.last_polled_at is not None                # timer stayed advanced -> backs off one interval


def test_cli_accepts_ingest_stage(monkeypatch):
    captured = {}
    def fake_run_worker(stage, **kwargs):
        captured["stage_name"] = stage.name
        return 0
    monkeypatch.setattr("bellwether.worker.run_worker", fake_run_worker)
    main(["ingest", "--once"])
    assert captured["stage_name"] == "ingest"


def test_cli_rejects_unknown_stage(monkeypatch):
    monkeypatch.setattr("bellwether.worker.run_worker", lambda stage, **kw: 0)
    with pytest.raises(SystemExit):
        main(["carrier_pigeon"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker_ingest.py -q`
Expected: FAIL with `ImportError: cannot import name 'make_ingest_stage' from 'bellwether.worker'`

- [ ] **Step 3: Add the stage builder and imports**

In `src/bellwether/worker.py`, add two imports near the other `from bellwether...` imports at the top (the `claim_*` imports are grouped in a `from bellwether.queue import (...)` block — add `claim_due_source` there; add the `ingest_source` import on its own line):

Change the queue import block from:

```python
from bellwether.queue import (
    claim_one, reclaim_stale, claim_due_impact, reclaim_stale_impacts,
    claim_pending_figure, reclaim_stale_figures,
    claim_pending_extraction, reclaim_stale_alerting,
)
```

to:

```python
from bellwether.queue import (
    claim_one, reclaim_stale, claim_due_impact, reclaim_stale_impacts,
    claim_pending_figure, reclaim_stale_figures,
    claim_pending_extraction, reclaim_stale_alerting,
    claim_due_source,
)
```

And add this import line alongside the other stage-logic imports (e.g. just below `from bellwether.alerts.engine import evaluate_extraction`):

```python
from bellwether.ingest import ingest_source
```

Then add the stage builder. Place it next to the other `make_*_stage` functions (e.g. immediately after `make_alert_stage`):

```python
def make_ingest_stage() -> Stage:
    def process(session: Session, source) -> None:
        # ingest_source fetches via build_connector, dedups by external_id, inserts
        # status="new" statements, and flushes. The claim already stamped last_polled_at
        # (stamp-first), so a fetch error here rolls back only these statements while the
        # advanced timer stands -> back off one interval. reclaim is a no-op by design.
        ingest_source(session, source)
        session.commit()

    return Stage(
        name="ingest",
        claim_next=lambda s: claim_due_source(s),
        reclaim=lambda s, secs: 0,
        process=process,
    )
```

- [ ] **Step 4: Wire `ingest` into `_build_stage` and the CLI**

In `src/bellwether/worker.py`, in `_build_stage(name)`, add an `ingest` branch BEFORE the final `return make_measure_stage(...)` fallthrough:

```python
    if name == "ingest":
        return make_ingest_stage()
    return make_measure_stage(build_market_data(), settings.measure_baseline_bars)
```

And extend the CLI `choices` in `main` from:

```python
    parser.add_argument("stage", choices=["detect", "extract", "resolve", "measure", "discovery", "alert"])
```

to:

```python
    parser.add_argument("stage", choices=["detect", "extract", "resolve", "measure", "discovery", "alert", "ingest"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker_ingest.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Run the discovery/ingest/queue/worker suites for regressions**

Run: `.venv/bin/python -m pytest tests/test_worker_ingest.py tests/test_queue_ingest.py tests/test_ingest.py tests/test_worker_discovery.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker_ingest.py
git commit -m "feat: ingest worker stage (python -m bellwether.worker ingest)"
```

---

### Task 3: Compose service + docs

**Files:**
- Modify: `docker-compose.yml` (add an `ingest` service mirroring `detect`)
- Modify: `docs/DEVELOPING.md` (add `ingest` to the worker command list)
- Modify: `docs/ARCHITECTURE.md` (add the `ingest` stage; note ingestion is now scheduled; 6 → 7 stages; note the stamp-first claim variant)

**Interfaces:**
- Consumes: `python -m bellwether.worker ingest` (Task 2).
- Produces: a deployable `ingest` service and accurate docs. No code.

- [ ] **Step 1: Add the `ingest` compose service**

In `docker-compose.yml`, add this service (identical shape to `detect`, which is:
```yaml
  detect:
    build: .
    command: python -m bellwether.worker detect
    env_file: .env
    depends_on:
      migrator:
        condition: service_completed_successfully
    restart: unless-stopped
```
). Add the new service alongside the other worker services:

```yaml
  ingest:
    build: .
    command: python -m bellwether.worker ingest
    env_file: .env
    depends_on:
      migrator:
        condition: service_completed_successfully
    restart: unless-stopped
```

- [ ] **Step 2: Verify compose config is valid**

Run: `docker compose config --quiet && echo OK`
Expected: `OK` (no YAML/schema error; prints nothing else)

- [ ] **Step 3: Update `docs/DEVELOPING.md`**

Find the worker list (the block of `python -m bellwether.worker <stage>` lines). After the `alert` line:

```
.venv/bin/python -m bellwether.worker alert        # evaluate alert rules -> webhooks + SSE
```

add:

```
.venv/bin/python -m bellwether.worker ingest       # poll enabled sources on their poll_interval -> new statements
```

- [ ] **Step 4: Update `docs/ARCHITECTURE.md`**

Locate the worker-stage enumeration (grep for `discovery` and `alert` in that file to find the stage list/description). Make three edits:
1. Add `ingest` to the stage enumeration with the description: "ingest — polls each enabled source on its `poll_interval_seconds` and creates `new` statements."
2. Change any "six stages"/"6 stages" reference to "seven stages"/"7 stages".
3. Add one sentence noting the stamp-first variant: "The `ingest` stage is a deliberate variant of the claim/reclaim pattern — it stamps `last_polled_at` at claim time as both schedule clock and claim guard, so it needs no in-flight status and no reclaim."

- [ ] **Step 5: Verify the docs reference the real stage set**

Run: `grep -c "ingest" docs/DEVELOPING.md docs/ARCHITECTURE.md`
Expected: each file returns `>= 1`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml docs/DEVELOPING.md docs/ARCHITECTURE.md
git commit -m "feat: ingest compose service + docs (pipeline now 7 stages)"
```

---

## Live verification (before merge, not a task)

With Postgres up and an enabled source present (e.g. the Donald Trump `x`/`rss` sources), run one real drain pass and confirm it polls and creates statements:

```bash
export $(grep -E '^(ANTHROPIC_API_KEY|TAVILY_API_KEY|X_API_KEY)=' .env | xargs)
.venv/bin/python -m bellwether.worker ingest --once
# then confirm sources.last_polled_at advanced and new statements appeared
```

Per AGENTS.md, this catches integration bugs the stubbed suite cannot (feed-window, auth, connector field mapping).

## Self-review notes

- **Spec coverage:** claim_due_source (Task 1) ✓; ingest stage + CLI (Task 2) ✓; no-migration/no-reclaim (Global Constraints + `reclaim=lambda s, secs: 0`) ✓; back-off-on-failure (Task 2 Step 1 test) ✓; compose service (Task 3) ✓; docs to 7 stages (Task 3) ✓; live verification (dedicated section) ✓. SKIP-LOCKED test intentionally omitted per codebase convention (documented in Global Constraints).
- **Type consistency:** `claim_due_source(session) -> Source | None` defined in Task 1, consumed in Task 2's `make_ingest_stage`; `make_ingest_stage() -> Stage` defined in Task 2, referenced by `_build_stage` and tests; `ingest_source(session, source)` used unchanged from existing `src/bellwether/ingest.py`.
- **No migration:** confirmed — the design adds no columns; `alembic` is untouched.
