# Ingestion Scheduler — Design

**Date:** 2026-07-11
**Status:** Approved (design)

## Problem

The pipeline has a gap between discovery and detection: **nothing polls the feeds.**
`bellwether.ingest.run_ingest_pass()` / `ingest_source()` are implemented and unit-tested,
but no running process ever calls them — there is no `ingest` worker stage, no compose
service, no scheduler, no API trigger. Confirmed: the only references to `run_ingest_pass`
in the repo are its definition and tests.

Consequently, discovered/enabled sources are never fetched: `sources.last_polled_at` stays
`NULL` forever, no `statements` are created, and the entire downstream pipeline
(detect → extract → resolve → measure → alert) is starved. The `sources.poll_interval_seconds`
column (default 300s) is a declared intention with no enforcer.

## Goal

Add a scheduler that polls each enabled source on its own `poll_interval_seconds` cadence,
creating `new` statements for the existing pipeline to consume — implemented as a new stage
in the existing worker harness.

## Decisions (locked)

1. **Placement:** a new `ingest` stage in the existing worker CLI
   (`python -m bellwether.worker ingest`), reusing the `Stage` / `run_worker` machinery
   (loop, `SKIP LOCKED` claiming, graceful shutdown, logging) — consistent with
   `detect`/`extract`/`resolve`/`measure`/`discovery`/`alert`.
2. **Failure handling:** on a failed poll (X rate-limit, feed 5xx, network error), **back off
   one interval** — the source is not retried until its next `poll_interval_seconds`. Errors
   are logged.

## Architecture — "stamp-first" claim

The back-off decision lets `last_polled_at` serve as *both* the schedule clock and the claim
guard, so **no new column, no migration, and no reclaim logic are needed.**

Mirroring `queue.claim_due_impact` (which claims `pending` impacts whose `due_at <= now()`),
add `queue.claim_due_source`:

```sql
SELECT * FROM sources
WHERE enabled
  AND (last_polled_at IS NULL
       OR last_polled_at + poll_interval_seconds * interval '1 second' <= now())
ORDER BY last_polled_at NULLS FIRST
FOR UPDATE SKIP LOCKED
LIMIT 1;
-- then: source.last_polled_at = now(); COMMIT; return source
```

Because the claim **stamps `last_polled_at = now()` and commits before the fetch runs**:

- **No double-poll** — once stamped, the source is not "due" again for a full interval, and
  `FOR UPDATE SKIP LOCKED` prevents two workers claiming the same row concurrently.
- **Back-off on failure (for free)** — the timer is advanced at claim time, so a fetch that
  throws leaves the source waiting a full interval before retry. Matches the locked decision.
- **Crash-safe** — a worker dying mid-fetch strands nothing (there is no in-flight status to
  get stuck on); the source simply polls next interval. Therefore `reclaim` is a **no-op**.

`last_polled_at` thus means "last *attempt*", which is the correct scheduling clock when
failures back off by a full interval.

### Scheduler granularity

The daemon wakes every `worker_poll_interval_seconds` (default 5s) to look for a due source,
so a source with `poll_interval_seconds = 300` is polled within ~5s of coming due. Claiming
one source per tick (as the other stages claim one row per iteration) is sufficient: when
multiple sources are due, `run_worker` loops back-to-back without sleeping until none remain.

## Components / files

- **`src/bellwether/queue.py`** — add `claim_due_source(session) -> Source | None` implementing
  the query above (mirrors `claim_due_impact`: `with_for_update(skip_locked=True)`,
  the due filter, order, `limit(1)`, then stamp `last_polled_at` and commit).
- **`src/bellwether/worker.py`**
  - add `make_ingest_stage() -> Stage` returning
    `Stage(name="ingest", claim_next=claim_due_source, reclaim=lambda s, secs: 0, process=…)`,
    where `process(session, source)` calls `ingest_source(session, source)` then
    `session.commit()`.
  - in `_build_stage`, handle `name == "ingest"` → `make_ingest_stage()` (no heavy/LLM build).
  - add `"ingest"` to the CLI `choices` list.
- **`src/bellwether/ingest.py`** — **unchanged.** `ingest_source` already fetches via
  `build_connector`, dedups by `external_id`, and inserts `status="new"` statements. Its own
  `last_polled_at` re-stamp on success is a harmless no-op given the claim already stamped it.
- **`docker-compose.yml`** — add an `ingest` service (same image as the other workers,
  `command: python -m bellwether.worker ingest`), gated on `migrator`/`db` like its siblings.

## Data flow

```
[ingest worker] every ~5s tick:
    claim_due_source
      └─ due source? → stamp last_polled_at=now, COMMIT (claim)
                     → ingest_source: connector.fetch()
                          → dedup vs existing external_ids
                          → INSERT new statements (status="new")
                          → COMMIT
                     └─ on any fetch error: rollback statements; timer already advanced
                        at claim → back off one interval; log the error; no crash
statements(new) → detect → extract → resolve → measure → alert   (existing pipeline)
```

## Error handling

- Per-source isolation: a failing source never blocks others — each claim/fetch/commit is a
  single source; the daemon moves to the next tick.
- The claim's `last_polled_at` stamp is the back-off mechanism; the fetch runs *after* the
  claim commit, so any exception in `connector.fetch()` or statement insertion rolls back only
  that source's statements, leaving the advanced timer intact.
- Errors are logged (`logger.exception`) with the source id; `run_worker`'s existing
  try/except already provides this envelope, and the process function commits its own success
  path.

## Testing (TDD)

**`tests/test_queue.py` (or a new `tests/test_queue_ingest.py`) — `claim_due_source`, real Postgres:**
- never-polled enabled source (`last_polled_at IS NULL`) is claimed.
- a source polled `< poll_interval_seconds` ago is **not** claimed.
- a source polled `> poll_interval_seconds` ago is claimed again.
- a disabled source is never claimed.
- claiming advances `last_polled_at` to ~now and commits.
- `SKIP LOCKED`: a row locked in one transaction is not returned to a second claimer.

**`tests/test_worker_ingest.py` — the `ingest` stage (integration, real Postgres, stub connector):**
- a due source is polled → `new` statements created, `last_polled_at` advanced.
- a not-yet-due source is skipped (no statements, timer unchanged).
- a connector whose `fetch()` raises → no statements, `last_polled_at` still advanced
  (back-off), worker does not crash.

**CLI:**
- `ingest` is accepted as a stage (`main(["ingest", "--once"])` runs a drain pass).
- unknown stages still rejected by argparse `choices`.

External connectors (RSS/X) remain stubbed in the suite; a real feed poll is verified live
before merge (per AGENTS.md — stubs hide integration bugs).

## Deployment & docs

- **No migration** — the design adds no columns, so there is nothing to `alembic upgrade`.
- **`docs/DEVELOPING.md`** — add `python -m bellwether.worker ingest` to the worker list.
- **`docs/ARCHITECTURE.md`** — document the ingest stage and note ingestion is now scheduled;
  the pipeline is now 7 stages (was 6). Note the stamp-first claim as a deliberate variant of
  the claim/reclaim pattern (no in-flight status, no reclaim).

## Out of scope (YAGNI)

- No manual "poll now" endpoint — the 5s tick polls newly-enabled sources almost immediately.
- No per-connector-type rate-limit floor — `poll_interval_seconds` per source is the control.
- No exponential backoff — flat one-interval back-off was chosen.
- No change to `sources.poll_interval_seconds` default (300s) or to `ingest_source`'s logic.
