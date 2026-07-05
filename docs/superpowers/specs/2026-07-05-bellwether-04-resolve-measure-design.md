# bellwether Plan 4 — Resolve & Measure — Design Spec

**Date:** 2026-07-05
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Parent spec:** `2026-07-04-bellwether-design.md` (§5.4 Resolve, §5.5 Measure, §7 firewall, §12 data model)
**Builds on:** Plan 1 (Foundation), Plan 2 (Ingestion), Plan 3 (LLM layer & queue harness)

---

## 1. Goal

Turn the structured signals produced by Plan 3 (`extractions` with an `entities` list) into
**measured market impact**. Two more non-Detect/Extract pipeline stages, both reusing Plan 3's
worker/queue harness:

- **Resolve** — map each extracted entity to a tradable **symbol**, verified against real market
  data. Writes `resolutions`; unverifiable entities are stored but flagged non-measurable.
- **Measure** (the "oracle") — after each event window elapses, pull price/volume around the
  statement's `published_at` for every resolved symbol and compute the realized move at
  configurable windows (default **5m / 1h / 1d**).

**Deviation from the parent spec, agreed with the owner:** §5.4 specified a hand-curated
entity→symbol map with LLM disambiguation deferred. Instead, Resolve uses an **LLM proposal +
deterministic yfinance verification + a self-building cache** (no hand-maintained map). The
deterministic verifier preserves the parent spec's anti-fabrication intent (§2). The pure
deterministic map remains a valid alternative behind the same contract.

**Out of scope (later plans):** alerts/notifications and the rule engine (Plan 7 — see §13);
golden sets / optimization / the Track-A metric path and its firewall assertion (Plan 5);
abnormal-return benchmarking, paid tick data, a DB/API-editable symbol map + review queue
(§13 deferred).

## 2. Hard invariants

- **No fabrication into the oracle.** Measure pulls the *real* price history of whatever symbol
  Resolve hands it — so a symbol may only be marked `measurable` when a deterministic verifier
  (yfinance) confirms it exists and its instrument name matches. The LLM proposes; **a
  deterministic check decides** — the same propose-vs-decide split as Extract's verbatim guard.
- **Firewall (§7).** `resolutions` and `impacts` are Track-B (impact) data. They never feed the
  Detect/Extract inputs. The asserting test ("market data never reaches Track A's metric path")
  lands in Plan 5, where Track A's metric path first exists; Plan 4 upholds it by construction.
- **DSPy-only, provider-agnostic.** Resolve's LLM is a DSPy module reached via LiteLLM; model is
  config-selected, credential from the env (no provider key in `Settings`). Consistent with Plan 3.
- **Shared corpus.** `resolutions`, `entity_symbols`, `impacts` carry **no `owner_id`**.
- **No live network in tests.** The LLM (DummyLM), the market-data adapter (replayed fixtures /
  injected fake), and the symbol verifier (stub) are all faked. The impact math is a pure function
  tested on synthetic series.

## 3. Pipeline integration & status

Resolve extends the `statements` status machine; Measure runs off a separate due-queue on
`impacts` (it cannot hang on `statements.status` because one statement fans out to many
symbol×window measurements that come due at different times).

```
(Plan 3) … extracted ──resolve──▶ resolving ──▶ resolved
                                                   │  Resolve also pre-creates, per
                                                   │  measurable resolution × window,
                                                   ▼  a `pending` impacts row (due_at set)
impacts:  pending ──measure (when due_at ≤ now)──▶ measuring ──▶ measured | measure_failed
```

- Statement statuses added: `resolving` (transient claim marker), `resolved` (terminal for the
  statement's pipeline). A statement with zero entities, or all-non-measurable entities, still
  reaches `resolved` (resolutions written; no `pending` impacts created).
- Impact statuses: `pending` → `measuring` (transient claim) → `measured` | `measure_failed`.

## 4. Data model

All three tables are shared corpus (no `owner_id`).

### 4.1 `resolutions`
One row per entity in an extraction.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `extraction_id` | FK→extractions.id (CASCADE, indexed) | |
| `entity` | str | the raw entity string from the extraction |
| `symbol` | str \| null | verified symbol, or null when non-measurable |
| `asset_class` | str \| null | `equity` / `etf` / `index` / `fx` / `crypto` |
| `measurable` | bool | true only if a symbol was verified |
| `created_at` | tz datetime, server default now() | |

### 4.2 `entity_symbols` (the self-building cache / map)
The resolver's memory — first time an entity is resolved, the verified result is cached here;
subsequent statements mentioning it skip the LLM+verify loop entirely. This *is* the map, built
from real traffic instead of by hand.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `normalized_entity` | str, **unique** | lowercased/stripped lookup key |
| `symbol` | str \| null | |
| `asset_class` | str \| null | |
| `measurable` | bool | cached verdict (a cached "no" avoids re-querying a known-unmappable entity) |
| `instrument_name` | str \| null | the verified instrument name (audit) |
| `confidence` | float \| null | model confidence at resolution time (audit) |
| `source` | str | e.g. `llm` (leaves room for `manual` later) |
| `created_at` | tz datetime, server default now() | |

### 4.3 `impacts` (result **and** due-queue)
One row per measurable resolution × window. Pre-created `pending` by Resolve; filled in by Measure.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `resolution_id` | FK→resolutions.id (CASCADE, indexed) | |
| `symbol` | str | denormalized from the resolution |
| `asset_class` | str | denormalized (routes the market adapter) |
| `window` | str | `5m` / `1h` / `1d` |
| `event_at` | tz datetime | = statement `published_at` (t0), denormalized so Measure needs no joins |
| `due_at` | tz datetime | = `event_at` + window; the measurement becomes computable at/after this |
| `status` | str | `pending` / `measuring` / `measured` / `measure_failed` |
| `price_t0` | float \| null | filled by Measure |
| `price_after` | float \| null | filled by Measure |
| `pct_move` | float \| null | `(price_after - price_t0) / price_t0` |
| `volume_spike` | float \| null | window volume ÷ baseline volume |
| `measured_at` | tz datetime \| null | when Measure computed it |
| `claimed_at` | tz datetime \| null | in-flight marker (crash recovery) |
| `created_at` | tz datetime, server default now() | |

Constraints/indexes: **unique `(resolution_id, window)`** (idempotent pre-creation); index on
`(status, due_at)` for the due-claim query.

## 5. Resolve stage

### 5.1 Contract & seam (consistent with Plan 3 §5.1)
- `ResolutionOutcome` — frozen dataclass: `symbol: str | None`, `asset_class: str | None`,
  `measurable: bool`, `instrument_name: str | None`, `confidence: float | None`.
- `Resolver` — `runtime_checkable` Protocol: attribute `model: str`, method
  `resolve(entity: str, context: ResolveContext) -> ResolutionOutcome` where `ResolveContext`
  carries the figure name and a statement-text snippet (disambiguation hints).
- `SymbolVerifier` — the deterministic arbiter: `verify(symbol, asset_class) ->
  InstrumentInfo | None` and `search(query) -> list[SymbolCandidate]` (both yfinance-backed).
  Injected, so tests stub it.
- `build_resolver(lm=None, verifier=None) -> Resolver` — wires the configured model + verifier +
  cache. The swap point (a future ReAct or pure-map resolver drops in here).

### 5.2 The bounded propose → verify → refine loop (LLM implementation)
`ResolveSig` (`dspy.Signature`): inputs `entity`, `context` (figure + snippet), optional
`feedback` (why the last attempt failed); outputs `symbol`, `asset_class`, `instrument_name`,
`confidence`, `is_tradable`. Wrapped in a `dspy.Module` (`dspy.Predict` / `ChainOfThought`) behind
the `Resolver` contract — **not** `dspy.ReAct`; the loop is code-owned and bounded.

Per unseen entity (cache miss):
1. Normalize the entity; **check `entity_symbols`** — a hit (measurable or not) short-circuits.
2. Attempt loop, up to `resolve_max_attempts`:
   a. LLM proposes `{symbol, asset_class, instrument_name, confidence, is_tradable}`.
   b. If `is_tradable` is false → stop, non-measurable.
   c. Deterministic **verify**: `verifier.verify(symbol, asset_class)` — must exist AND its real
      instrument name fuzzy-matches the proposed name AND `confidence ≥ resolve_confidence_threshold`.
      Pass → accept (measurable). Fail → optionally `verifier.search(entity/name)` for candidates,
      feed the failure + candidates back as `feedback`, and retry.
3. On accept → `ResolutionOutcome(measurable=True, …)`; on give-up → `measurable=False`.
4. **Final gate:** `measurable=True` is only ever returned when the accepting attempt's verify
   passed — an unverified symbol can never be accepted. Write the verdict to `entity_symbols`
   (cache both yes and no; on a concurrent unique-key clash, treat as a cache hit).

### 5.3 Stage behavior
`make_resolve_stage(resolver, windows)` claims `extracted → resolving` (via Plan 3's `claim_one`).
`process`:
- Load the statement's `Extraction`; for each `entity`, call `resolver.resolve(...)` and write a
  `resolutions` row.
- For each **measurable** resolution, insert `pending` `impacts` rows — one per configured window —
  with `event_at = statement.published_at`, `due_at = event_at + window`.
- Set statement `resolved`, clear `claimed_at`, commit.

## 6. Market-data adapter

One interface, `MarketData`, routing by `asset_class`:
- **Verification/lookup** (used by Resolve's verifier): `lookup(symbol) -> InstrumentInfo | None`,
  `search(query) -> list[SymbolCandidate]`.
- **Price series** (used by Measure): `price_series(symbol, asset_class, start, end) ->
  PriceSeries` (timestamped price + volume bars).
- Implementations: **`YFinanceAdapter`** (equity/etf/index/fx) + **`CoinGeckoAdapter`** (crypto),
  behind a `MarketData` facade/registry that dispatches on `asset_class`. Neither needs an API key
  (free tiers, keyless).
- **Tests use replayed fixtures — no live network.** Stage/e2e tests inject a fake `MarketData`;
  the adapter's own parsing is tested against recorded/synthetic responses.

**Honest free-data limitation:** intraday granularity (yfinance 5m/1h) is only available for recent
dates; older statements can measure `1d` but not the sub-hour windows. When the adapter can't
supply data for a window, the impact math reports insufficient data and Measure sets
`measure_failed` for that row (§8). Sub-minute windows are not offered (parent spec §5.5).

## 7. Impact math (pure function)

`compute_impact(series: PriceSeries, event_at, window, baseline) -> ImpactPoint | None`:
- `price_t0` = last bar at/just-before `event_at`; `price_after` = first bar at/just-after
  `event_at + window`.
- `pct_move = (price_after - price_t0) / price_t0`.
- `volume_spike = (volume over the window) / (baseline average volume)`; baseline = average
  per-bar volume over a configured number of bars before `event_at`.
- Returns `None` (insufficient data) when the series lacks bars to bracket `event_at` or the window
  end. **Pure** — no I/O; tested on synthetic series (rise, fall, flat, gap/market-closed, exact
  boundary).

## 8. Measure stage

Runs off the `impacts` due-queue, not `statements`.
- **Claim:** a new `claim_due_impact(session, to_status="measuring")` in the queue harness —
  `SELECT … FROM impacts WHERE status='pending' AND due_at <= now() ORDER BY due_at FOR UPDATE
  SKIP LOCKED LIMIT 1`, flip to `measuring`, set `claimed_at`, commit. Same lock-then-release
  discipline as Plan 3's `claim_one`.
- **`process`:** fetch the price series around `event_at` via the market adapter, call
  `compute_impact`; on a result → fill `price_t0/price_after/pct_move/volume_spike`, set `measured`,
  `measured_at=now()`; on insufficient data or a transient adapter error → see §9.
- **Recovery:** `reclaim_stale` variant resets `measuring` impacts older than the cutoff back to
  `pending`.

## 9. Errors, retry, and terminal semantics

Following Plan 3's terminal-vs-retryable split:
- **Insufficient data** (the window genuinely can't be measured from free data) → terminal
  `measure_failed` (retrying won't help).
- **Transient adapter error** (network/timeout/rate-limit) → propagate; the worker rolls back and
  leaves the row `measuring` for reclaim-retry — it is not burned to `measure_failed`.
- Resolve mirrors this: a give-up after the bounded loop is a normal non-measurable result (not an
  error); a transient LLM/verifier error propagates for reclaim-retry of the statement.
- The market adapter, like `worker.py`, exposes failures as paradigm-agnostic exceptions so the
  stage/worker layer never imports yfinance/coingecko internals (mirrors Plan 3's
  `ExtractionParseError` seam).

## 10. Worker harness generalization

Plan 3's `run_worker` hardcodes a `claim_one(statements)` claim. Plan 4 generalizes `Stage` to
carry its own `claim_next(session) -> row | None` and a per-stage `reclaim()` — so a status-claim
stage (Resolve, on `statements`) and a due-claim stage (Measure, on `impacts`) share the same loop,
backoff, graceful shutdown, and periodic reclaim. Detect/Extract are rewrapped to the new `Stage`
shape (behavior unchanged). CLI gains `resolve` and `measure`:
`python -m bellwether.worker {detect|extract|resolve|measure} [--once]`.

## 11. Configuration (env, mirroring Plan 3)

Add to `Settings` (defaults shown): `resolve_model = "anthropic/claude-haiku-4-5"`,
`resolve_max_attempts = 3`, `resolve_confidence_threshold = 0.5`,
`measure_windows = "5m,1h,1d"`, `measure_baseline_bars = 20`. No new credential — yfinance and
CoinGecko are keyless; the LLM credential is the existing provider env var.

## 12. Testing

- **Resolve loop** — DummyLM + a stub `SymbolVerifier`: accept on a verified propose; retry-then-
  accept after one failed verify; give-up → non-measurable; a cache hit skips the LLM. No network.
- **Cache** — a second statement with the same entity resolves from `entity_symbols` (LLM not called).
- **Impact math** — pure unit tests on synthetic series (rise/fall/flat/gap/boundary/insufficient).
- **Market adapter** — replayed fixtures; parsing only, no live calls.
- **Queue** — `claim_due_impact` respects `due_at` (a not-yet-due `pending` row isn't claimed;
  a due one is) and SKIP LOCKED (two connections don't double-claim); reclaim resets stale `measuring`.
- **Resolve stage** — real Postgres + stubs: writes `resolutions` + `pending` impacts (with correct
  `due_at`) for measurable entities; none for non-measurable; statement → `resolved`.
- **Measure stage** — stub adapter: a due row → `measured` with computed fields; insufficient data →
  `measure_failed`; transient error → stays `measuring` (retryable).
- **End-to-end** — seed an `extracted` statement → resolve `--once` → (advance clock / due rows) →
  measure `--once` → assert `resolutions` + `measured` impacts.
- **Firewall (structural)** — assert `resolutions`/`impacts` are not read by any Detect/Extract input
  path; the metric-path assertion is Plan 5.

## 13. Deferred with intent

- Notifications/alerts on signals (post-Extract/Resolve) and on realized impact (post-Measure) —
  the rule engine + webhook + SSE are **Plan 7**. Plan 4 only produces the data those alerts read.
- Richer resolver (`dspy.ReAct` with tools, or web search; or the pure deterministic map) — swappable
  behind the `Resolver` contract.
- LLM-as-judge / human review & correction of `entity_symbols`; DB/API-editable map + review queue.
- Re-resolving non-measurable rows when the resolver/map improves.
- Abnormal-return benchmarking (subtract sector/benchmark), paid tick data for fine intraday windows.
- Golden sets, optimization, and the Track-A firewall **assertion** — Plan 5.
