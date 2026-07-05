# bellwether Plan 3 — LLM Layer (Detect + Extract) & Queue Harness — Design Spec

**Date:** 2026-07-05
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Parent spec:** `2026-07-04-bellwether-design.md` (§5.2 Detect, §5.3 Extract, §6 LLM layer, §4 orchestration)
**Builds on:** Plan 1 (Foundation), Plan 2 (Ingestion & Watchlist)

---

## 1. Goal

Turn ingested `statements` into structured market signals through two LLM stages —
**Detect** (cheap relevance gate) and **Extract** (structured signal) — driven by a
generic Postgres-backed **worker/queue harness** (`FOR UPDATE SKIP LOCKED`) that was
deferred from Plan 2. Every extracted `evidence_quote` is enforced in code to be a
verbatim substring of the statement (the structural anti-fabrication guarantee).

**Explicitly out of scope (later plans):** golden sets, `dspy.compile` optimization,
champion/challenger promotion, `dspy_programs`/`eval_runs`/`*_labels` tables (Plan 5);
entity→symbol resolution and impact measurement (Plan 4). Plan 3 runs **baseline**,
un-optimized DSPy programs (`version="baseline"`).

## 2. Hard invariants carried from the parent spec

- **DSPy-only, model-agnostic via LiteLLM.** No LangChain/LangGraph. Each LLM stage is
  a **single call** (§6). Multi-step agents (ReAct, draft→critique→refine) are the
  deferred §18 upgrade — see §5.4.
- **Verbatim-substring guard.** A non-substring `evidence_quote` is rejected in code;
  no extraction row is written. Enforced at the stage boundary, independent of the DSPy
  paradigm (§5.5).
- **Shared corpus.** `detections` and `extractions` carry **no `owner_id`** (symmetric
  with `statements`).
- **Read-only, no generation.** The layer only reads statement text and emits structure;
  it never fabricates or rewrites statements.
- **Tests hit real Postgres; no live network.** The LLM is the only network call and is
  replaced by an injected fake LM in tests (§8).

## 3. Data model

Two new tables (both shared corpus — no `owner_id`), plus a small alter to `statements`.

### 3.1 `detections`
One row per statement per detect run.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `statement_id` | FK→statements.id (CASCADE, indexed) | |
| `is_relevant` | bool | model's relevance verdict |
| `score` | float | 0–1 relevance confidence |
| `model` | str | e.g. `anthropic/claude-haiku-4-5` |
| `version` | str | `"baseline"` for Plan 3 |
| `created_at` | tz datetime, server default now() | |

### 3.2 `extractions`
One row per statement per successful extract run.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `statement_id` | FK→statements.id (CASCADE, indexed) | |
| `entities` | JSONB | list of strings |
| `direction` | str | `up` / `down` / `neutral` |
| `magnitude` | str | `none` / `small` / `moderate` / `large` |
| `confidence` | float | 0–1 |
| `evidence_quote` | text | **verbatim** substring of `statement.text` |
| `model` | str | e.g. `anthropic/claude-sonnet-5` |
| `version` | str | `"baseline"` |
| `created_at` | tz datetime, server default now() | |

### 3.3 `statements` alter
Add `claimed_at` (nullable tz datetime) to support crash-recovery of in-flight rows
(§4.2). The existing `status` column (indexed) is the queue key.

### 3.4 Status vocabulary (the pipeline state machine)

```
new ──detect──▶ irrelevant                (is_relevant false / score < threshold → terminal)
            └──▶ detected ──extract──▶ extracted
                                    └──▶ extract_failed   (guard or schema/parse failure)
```

Transient in-flight markers, held only while a worker owns the row:
`detecting` (claimed from `new`), `extracting` (claimed from `detected`). A worker sets
these at claim time and advances to a terminal status when done. `reclaim_stale`
(§4.2) resets rows stuck in an in-flight marker past a timeout.

Full status set: `new`, `detecting`, `detected`, `irrelevant`, `extracting`,
`extracted`, `extract_failed`.

## 4. Generic queue harness — `bellwether/queue.py`

The reusable status-claiming primitive the parent spec assigns to Plan 3. Stage-agnostic:
it knows only about `Statement.status`, not about Detect/Extract.

### 4.1 `claim_one(session, from_status, to_status) -> Statement | None`
```sql
SELECT … FROM statements
 WHERE status = :from_status
 ORDER BY published_at
 FOR UPDATE SKIP LOCKED
 LIMIT 1
```
Then flip `status = to_status`, set `claimed_at = now()`, **commit**, and return the row
(or `None` if the queue is empty). SQLAlchemy: `select(Statement).where(...).order_by(...)
.with_for_update(skip_locked=True).limit(1)`.

**Key correctness property:** the row lock is released by the commit *before* the slow
LLM call runs — a worker never holds a row lock across network I/O. Two concurrent
workers never claim the same row (`SKIP LOCKED`). Directly tested against real Postgres.

### 4.2 `reclaim_stale(session, in_status, to_status, older_than) -> int`
Resets rows stuck in an in-flight marker (`detecting`/`extracting`) whose `claimed_at`
is older than `older_than` back to their queue status (crash recovery). Returns count
reset. Run on worker startup and periodically in the loop.

## 5. LLM layer — `bellwether/llm/`

### 5.1 Stable contract vs. swappable body (the modular seam)

The DSPy paradigm is an implementation detail behind a **frozen interface**, so a future
paradigm swap (ReAct, draft→critique→refine) touches exactly one class and nothing
downstream.

- **Contracts (frozen):**
  - `DetectionResult(is_relevant: bool, score: float)`
  - `ExtractionResult(entities: list[str], direction: str, magnitude: str,
    confidence: float, evidence_quote: str)`
  - `Detector` — anything with `detect(statement_text: str) -> DetectionResult`.
  - `Extractor` — anything with `extract(statement_text: str) -> ExtractionResult`.
- **Bodies (swappable):** `dspy.Module` subclasses. Today Detect = `dspy.Predict`,
  Extract = `dspy.ChainOfThought`. Tomorrow either can be replaced with a different
  Module (e.g. ReAct/refine) without changing the worker, queue, guard, persistence,
  or tests.
- **Factories:** `build_detector()` / `build_extractor()` construct the configured
  implementation (model + paradigm) from settings — the one-line swap point.

The worker, queue, guard, persistence, and Plan-5 eval hooks all depend only on the
contracts.

### 5.2 `config.py`
Configures the DSPy LM from settings via LiteLLM. Detect and Extract get **separate LM
instances** (different models). The LLM stays **provider-agnostic** — the model strings
(`detect_model`/`extract_model`) select the provider, and LiteLLM reads whatever
credential that provider needs directly from the environment (e.g. `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, a Vertex/Bedrock credential, …). No provider key is modeled in
`Settings` (credentials → env), so switching providers is a model-string + env-var
change with no code change.

### 5.3 Detect — `detect.py`
`dspy.Signature`: `statement_text: str → is_relevant: bool, score: float`. Wrapped in a
`dspy.Module` using **`dspy.Predict`** (single call, no reasoning field — right for the
cheap gate that runs on *every* statement on Haiku). Returns a `DetectionResult`.

### 5.4 Extract — `extract.py`
`dspy.Signature`: `statement_text: str → entities: list[str],
direction: Literal["up","down","neutral"], magnitude: Literal["none","small","moderate","large"],
confidence: float, evidence_quote: str`. Wrapped in a `dspy.Module` using
**`dspy.ChainOfThought`** (still one LLM call — CoT adds only a `reasoning` output field
before the structured fields; it materially helps the harder extraction task and helps
the model select a genuine verbatim quote). Returns an `ExtractionResult`.

**Why not ReAct / multi-step now:** ReAct exists to interleave reasoning with *tool
calls*; Extract is a closed-book transform with no tools to call (the statement is fully
in context, and entity→symbol lookup is a separate deterministic stage — Resolve, Plan
4). Multi-step would multiply cost/latency per statement with no information gain, is
harder to optimize/evaluate behind the accuracy firewall, and adds nothing the substring
guard doesn't enforce more strongly. Draft→critique→refine is the deferred §18 upgrade,
added later with its own eval — and the modular seam (§5.1) makes that swap trivial.

### 5.5 Verbatim-substring guard — `guard.py`
Pure Python, no LLM: `is_verbatim(quote: str, source_text: str) -> bool`. Applied by the
**stage boundary** (§6), *after* the extractor returns and *outside* the swappable
Module — so the anti-fabrication guarantee holds no matter which paradigm is plugged in.
A failing guard (or a schema/parse error from DSPy) → **no `extractions` row** written,
statement status → `extract_failed`. Unit-tested independently of any LLM.

## 6. Worker runtime — `bellwether/worker.py` + CLI

A generic loop wrapping a **stage**. A stage is a small object declaring its
`from_status`/in-flight/terminal statuses and a `process(session, statement)` that runs
the LLM work via the §5.1 contract, applies the guard/threshold at the boundary, writes
the result row, and sets the terminal status.

- **`run_worker(stage, once=False)`** — `reclaim_stale` on startup, then loop:
  `claim_one` → `process` → repeat. When the queue is empty, sleep
  `worker_poll_interval_seconds` (light backoff). Handles `SIGINT`/`SIGTERM` by
  finishing the in-flight item, then exiting cleanly — no half-processed rows.
- **`--once`** drains the queue in a single pass and exits (used by tests and manual
  runs). Default is the long-running daemon.
- **CLI:** `python -m bellwether.worker detect` / `python -m bellwether.worker extract`.

### 6.1 Stage semantics
- **Detect stage:** claims `new → detecting`; runs `detector.detect(text)`; writes a
  `detections` row; sets `detected` if `is_relevant and score ≥ relevance_threshold`,
  else `irrelevant`.
- **Extract stage:** claims `detected → extracting`; runs `extractor.extract(text)`;
  applies the substring guard; on pass writes an `extractions` row and sets `extracted`;
  on guard/parse failure writes no row and sets `extract_failed`.

Each `process` is idempotent-friendly: it operates only on a row it has exclusively
claimed, and advances the row out of the claimable state before the terminal write.

## 7. Configuration (env, pydantic-settings)

Add to `Settings` (with defaults):
- `detect_model = "anthropic/claude-haiku-4-5"`
- `extract_model = "anthropic/claude-sonnet-5"`
- `relevance_threshold = 0.5`
- `worker_poll_interval_seconds = 5`
- `worker_stale_reclaim_seconds = 300`

The LLM **provider credential** is a standard env var LiteLLM reads for whichever
provider the configured model strings point at — **not** a `Settings` field, and
**not** hard-coded to any one provider. Update `.env.example` with the model knobs and a
commented, provider-neutral note (e.g. "set the credential your `*_model` provider
needs — `ANTHROPIC_API_KEY` for the default Claude models, `OPENAI_API_KEY` for OpenAI,
etc.").

## 8. Testing (real Postgres, no live network)

- **Guard** — pure unit tests: substring accept/reject and normalization edges.
- **Queue** — real-Postgres: `claim_one` claim-once under concurrency (two claims never
  return the same row), `reclaim_stale`, and each status transition.
- **DSPy stages** — inject a **fake LM** (`dspy.utils.DummyLM`, or a thin injectable
  stub if `DummyLM` proves awkward) returning canned completions. Assert Detect routes
  relevant vs. irrelevant/below-threshold correctly, and Extract writes a valid row /
  fails the guard on a fabricated (non-substring) quote. **No provider key or network.**
- **Worker** — with a fake LM: `run_worker(stage, once=True)` drains a seeded row.
- **End-to-end** — seed a `new` statement → detect `--once` → extract `--once` → assert
  `detections` + `extractions` rows and final `extracted` status.

## 9. Dependencies & file layout

Add `dspy` to `pyproject.toml` (pulls in LiteLLM).

```
src/bellwether/
├── models/
│   ├── detection.py            # Detection model
│   └── extraction.py           # Extraction model
├── queue.py                    # claim_one + reclaim_stale (generic)
├── worker.py                   # run_worker loop + stage defs + CLI (__main__)
├── llm/
│   ├── __init__.py
│   ├── config.py               # DSPy LM setup from settings (LiteLLM)
│   ├── contracts.py            # DetectionResult, ExtractionResult, Detector, Extractor
│   ├── detect.py               # DetectSig + Detect module + build_detector()
│   ├── extract.py              # ExtractSig + Extract module + build_extractor()
│   └── guard.py                # is_verbatim(...)
└── config.py                   # (modify) add model/threshold/worker settings
migrations/versions/            # two migrations: new tables; statements.claimed_at
tests/
├── test_queue.py
├── test_worker.py
├── llm/
│   ├── test_guard.py
│   ├── test_detect.py
│   └── test_extract.py
└── test_pipeline_e2e.py
```

## 10. Deferred with intent

- Optimization flywheel, golden sets, champion/challenger, `dspy_programs`/`eval_runs`/
  `relevance_labels`/`extraction_labels` — Plan 5.
- Multi-step Extract (draft→critique→refine / ReAct) — parent spec §18; enabled by the
  §5.1 modular seam.
- Polling **scheduler** that triggers ingest on `poll_interval` — the worker daemon here
  consumes the queue; a scheduler that *fills* it on a cadence is a later concern.
- Entity→symbol Resolve and impact Measure — Plan 4.
