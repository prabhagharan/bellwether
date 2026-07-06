# bellwether Plan 6 — Source Discovery — Design Spec

**Date:** 2026-07-06
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Parent spec:** `2026-07-04-bellwether-design.md` (§8 Source discovery & configuration)
**Builds on:** Plan 2 (figures/sources/statements, connector registry + RSS connector, owner-scoped watchlist API, ingestor), Plan 3 (DSPy contracts + `build_*()` factories, SKIP-LOCKED queue harness), Plan 5 (review-and-correct pattern; the champion-loading seam that can later optimize the Discovery module).

---

## 1. Goal

Make adding a figure *by name* auto-discover its verified sources, instead of requiring manual source entry. A **deterministic Wikidata backbone** establishes the trustworthy bindings; a **DSPy Discovery module + Tavily web search** handle the fuzzy parts (disambiguation, gap-fill); **deterministic cross-reference verification** gates each binding into auto-enabled or a **`pending_review` queue**. Plus a real **X connector** (ingestion poller, ships disabled until keyed).

The review queue's confirm/reject actions double as byproduct labels for optimizing the Discovery module later (a symmetric future application of Plan 5's flywheel — not built here).

**In scope:** the `discovery` worker stage (Wikidata + LLM disambiguation + Tavily gap-fill + verification + confidence gate); the extended `figures`/`sources` model; the review-queue API; the X ingestion connector; all external clients behind stubbable seams.

**Out of scope (deferred with intent):** a dedicated YouTube connector (YouTube's per-channel RSS feed covers it via the existing RSS connector); **optimizing** the Discovery module (Plan 6 records the confirm/reject labels via the source lifecycle but does not run the flywheel); a `discovery_labels` table (until we optimize Discovery); scheduled/automatic re-discovery; additional Wikidata properties or source types.

## 2. Hard invariants

- **The LLM proposes, deterministic verification disposes.** A Discovery-module or Tavily-proposed candidate can *never* auto-enable itself; only cross-referenced independent authorities (§8) clear the gate. This preserves the provenance guarantee — the read-only/verified-source boundary from the master spec.
- **Deterministic backbone.** Wikidata lookup + the cross-reference confidence model are pure/deterministic and testable without an LLM. The LLM only disambiguates and gap-fills.
- **Reachability before trust** (anti-fabrication analog): a feed/URL must actually resolve and parse before it earns the reachability signal.
- **`worker.py` stays clean.** The discovery stage's `process()` calls a `discovery/pipeline.py` orchestrator over injected clients; `worker.py` imports no Wikidata/Tavily/DSPy code. Transient external failures raise a paradigm-agnostic `DiscoveryError` (retryable `failed`); "no match" is terminal `done`.
- **Keyless by default, keys optional.** Discovery runs with no keys (Wikidata is keyless; Tavily needs `TAVILY_API_KEY` for gap-fill; X verify + the X connector need `X_API_KEY`). Missing optional keys degrade gracefully, they don't break discovery. Credentials are plain env vars, never `Settings` fields (provider-agnostic pattern).
- **Owner scoping.** The review queue and review submit are owner-scoped by figure ownership. `figures`/`sources` keep their nullable `owner_id`.
- **Idempotent re-runs.** Discovery upserts bindings by a natural key; a retried or re-triggered run never duplicates sources, and never touches `origin="manual"` sources.
- **Tests: real Postgres, no live network.** All external clients + the Discovery module are stubbed behind seams; the pure confidence/mapping logic is unit-tested; live Wikidata/Tavily/X are verified manually (stubs can't catch integration bugs — same discipline as the yfinance market adapter).

## 3. Architecture & flow

A new **`discovery` worker stage**. Adding a figure by name creates it immediately with `discovery_status="pending"`; the worker claims it via the existing `FOR UPDATE SKIP LOCKED` harness (generalized to claim *figures* by `discovery_status`, exactly as Measure generalized it to claim due impacts).

```
POST /figures {name:"Jerome Powell", discover:true}   ->   figures(discovery_status=pending)
        │
   discovery worker claims (SKIP LOCKED on discovery_status + discovery_claimed_at)
        │
   1. Wikidata backbone (deterministic): search name -> candidate entities; pick QID;
      fetch P856 website, P2002 X, P2397 YouTube, aliases/labels
   2. Discovery module (LLM): disambiguate the entity if ambiguous; propose gap-fill
      source candidates Wikidata lacks (using Tavily results as context)
   3. Connector mapping: YouTube channel -> RSS feed URL; website -> RSS feed auto-discovery;
      X handle -> x connector binding (disabled w/o key)
   4. Verification (deterministic cross-reference): Wikidata claim + official-domain
      agreement + (optional) X verified-status + feed reachability -> discovery_confidence
   5. Gate: confidence >= threshold -> status=active, enabled=true, verified=true;
      below -> status=pending_review, enabled=false
        │
   figures(discovery_status=done) + sources written
        │
   GET /discovery/queue -> human confirm/reject pending_review -> status active|rejected
                            (byproduct discovery labels for optimizing Discovery later)
```

## 4. Data model

**No new tables.** Discovery rides on `figures` + `sources`.

### 4.1 `figures` (add columns)
| Column | Type | Notes |
|---|---|---|
| `wikidata_id` | str, nullable, indexed | resolved QID (`Q...`); null if unresolved |
| `discovery_status` | str | `pending` → `running` → `done` / `failed`; `skipped` for manual-only figures |
| `discovery_claimed_at` | tz datetime, nullable | SKIP-LOCKED claim stamp (like `statements.claimed_at`) |
| `discovery_error` | text, nullable | last failure reason |

### 4.2 `sources` (add columns; on top of Plan 2's `connector_type`/`config`/`provenance`/`origin`/`enabled`/`poll_interval`/`owner_id`)
| Column | Type | Notes |
|---|---|---|
| `status` | str | `active` / `pending_review` / `rejected` (default `active`) |
| `verified` | bool | passed deterministic cross-reference (default false) |
| `discovery_confidence` | float, nullable | the cross-reference score that drove the gate |
| `discovery_meta` | JSONB, nullable | signal breakdown, e.g. `{wikidata:true, domain_match:true, x_verified:null, reachable:true, source:"wikidata"}` |

Migration adds these with safe defaults so existing Plan-2 sources become `status="active"`, `verified=false`, `discovery_confidence=null`.

**A "candidate" is a `sources` row with `status="pending_review"`.** Confirming/rejecting it flips `status` — that lifecycle *is* the byproduct discovery label (confirmed = positive, rejected = negative). No `discovery_labels` table until we optimize Discovery.

**Dedup:** discovery upserts by natural key (`figure_id` + `connector_type` + a config-derived identity like the handle/feed URL), so re-runs never duplicate. `origin="manual"` sources are never touched.

## 5. External seams + the Discovery module

Everything network- or LLM-facing sits behind a protocol + `build_*()` factory (the market/LLM seam pattern); orchestration lives in `discovery/`.

- **`discovery/wikidata.py`** — `WikidataClient` protocol + adapter + `build_wikidata()`. Keyless. `search(name) -> [WikidataEntity(qid, label, description)]`; `claims(qid) -> {website, x_username, youtube_channel, aliases}` (P856/P2002/P2397 + labels/aliases).
- **`discovery/websearch.py`** — `WebSearch` protocol + `TavilyAdapter` + `build_web_search()` + registry (mirrors `market/registry.py`). `search(query) -> [SearchResult(title, url, snippet)]`. `TAVILY_API_KEY` in env; swappable.
- **`discovery/xverify.py`** — `XVerifier` protocol + adapter + `build_x_verifier()`. `verify(handle) -> XStatus(exists, verified) | None` (None when no `X_API_KEY`).
- **`discovery/discoverer.py`** — the DSPy **Discovery module** behind a frozen `Discoverer` contract + `build_discoverer()` (symmetric with Detect/Extract/Resolve). `disambiguate(name, candidates) -> Disambiguation(qid, confidence)`; `gapfill(figure, known_sources, search_results) -> [SourceCandidate]`. Un-optimized baseline; `discovery_model` in Settings; Plan 5's champion-loading seam can later load an optimized program via `build_discoverer(program_state=…)`.
- **`discovery/connectors.py`** — pure mapping: YouTube channel id → RSS feed URL (`youtube.com/feeds/videos.xml?channel_id=…`); website → RSS feed auto-discovery (`<link rel="alternate" type="application/rss+xml">` / common paths); X handle → `x` binding. Returns `(connector_type, config)`.
- **`discovery/verify.py`** — pure `score_binding(signals) -> (confidence, verified)` (§8).
- **`discovery/pipeline.py`** — `run_discovery(session, figure, *, wikidata, web_search, x_verifier, discoverer) -> list[SourceBinding]` orchestrates steps 1–5 over injected clients; returns bindings + statuses. The worker stage's `process()` calls it.

Contracts + `DiscoveryError` live in `discovery/contracts.py`; the worker imports only those (paradigm-agnostic).

## 6. The Discovery module (DSPy)

- **Disambiguate:** inputs `name` + candidate entities (QID/label/description); output the chosen `qid` + a `confidence` 0..1. `dspy.Predict` baseline. Low confidence + multiple viable candidates → the figure's bindings all go `pending_review` (human confirms identity by confirming sources); `discovery_status=done`.
- **Gap-fill:** inputs the figure + known sources + Tavily results; output additional `SourceCandidate`s (connector_type + config + rationale). `dspy.Predict`/`ChainOfThought` baseline. These are *proposals* — they still pass §8 verification before auto-enabling.
- Behind `Discoverer` contract + `build_discoverer()`; `DummyLM`-tested.

## 7. Connector mapping + the X connector

- **YouTube channel → RSS**: mapped to the existing RSS connector via the channel's feed URL. No new poller.
- **Website → RSS**: feed auto-discovery; if a feed is found, an RSS binding; else a stored non-polling `website` record (`enabled=false`).
- **X handle → X connector**: a new **ingestion connector** registered in Plan 2's connector registry — `fetch(config) -> [RawItem]` fetching a handle's recent posts via the X API, same interface as the RSS connector. **Ships disabled** (`enabled=false`) until `X_API_KEY` is set. Stubbed in tests, live-verified. (Distinct from `XVerifier`, which only checks account status for discovery confidence.)

## 8. Verification & confidence (deterministic)

Per-binding independent signals, each verifiable in code:
- **Wikidata claim** (+0.6) — came from a structured `P856`/`P2002`/`P2397` claim.
- **Official-domain agreement** (+0.3) — the binding's domain matches the figure's official website domain (or the official site links it).
- **X verified-status** (+0.2) — X bindings only, when a key is set: real, verified account.
- **Reachability** (+0.2) — the feed URL resolves and parses as a valid feed.

`discovery_confidence` = sum of a binding's signals, clamped to 1.0. **Gate:** `discovery_confidence >= discovery_confidence_threshold` (config, default **0.7**) → `verified=true, status=active, enabled=true`; below → `status=pending_review, enabled=false`. Weights are set so a **single signal stays below the bar** and **two-plus corroborating signals (≥0.7) clear it** — the spec's "trusted only when independent authorities agree," expressed as a threshold (consistent with `relevance_threshold`/`resolve_confidence_threshold`). `discovery_meta` records which signals fired.

Resulting behavior:
- **Wikidata website, feed reachable** = 0.6 + 0.2 = 0.8 → `active`.
- **Wikidata X-handle, no X key** = 0.6 alone → `pending_review` unless the official domain corroborates (+0.3 = 0.9 → `active`).
- **Tavily-proposed feed** (no Wikidata claim): domain-match + reachable = 0.3 + 0.2 = 0.5 → **still `pending_review`**. An LLM-proposed source needs strong corroboration to reach 0.7 (e.g. an X handle with domain-match + X-verified + reachable = 0.3 + 0.2 + 0.2 = 0.7 → `active`). So weakly-corroborated LLM proposals are **human-confirmed by default** — the invariant §2 in action.

**Edge cases (terminal, not errors):** ambiguous identity → all bindings `pending_review`, `discovery_status=done`; no Wikidata match → LLM+Tavily gap-fill only (low-confidence → `pending_review`) or zero bindings → `done` empty (user adds manually).

## 9. API surface + worker CLI

**API (authenticated):**
- `POST /figures` (Plan-2, extended) — `{name, type, discover: true}`. `discover=true` (default) → `discovery_status="pending"`; `false` → `"skipped"`.
- `POST /figures/{id}/discover` — (re)trigger: set `discovery_status="pending"` (idempotent; dedup prevents duplicate bindings).
- `GET /figures/{id}` (extended) — returns `discovery_status`, `wikidata_id`; its sources carry `status`/`verified`/`discovery_confidence`/`discovery_meta`.
- `GET /discovery/queue?limit=` — `pending_review` sources for the caller's figures (figure name, connector, config, confidence, `discovery_meta`). Owner-scoped.
- `POST /discovery/{source_id}` — `{decision: "confirm"|"reject"}`. confirm → `active`/`enabled=true`/`verified=true`; reject → `rejected`/`enabled=false`. 404 if the source's figure isn't owned.

**Worker CLI:** `python -m bellwether.worker discovery` — new stage in `choices=["detect","extract","resolve","measure","discovery"]`, claiming figures via the generalized SKIP-LOCKED harness.

Manual source-add (Plan 2) unchanged: `origin="manual"`, `status="active"`, untouched by discovery.

## 10. Configuration

`Settings`: `discovery_model` (str, e.g. `anthropic/claude-sonnet-5` — disambiguation/gap-fill LLM), `discovery_confidence_threshold` (float, default 0.7). Worker poll/reclaim settings reuse the existing ones.

Credentials (plain env, never `Settings` fields): `TAVILY_API_KEY` (gap-fill; no key → gap-fill skipped, Wikidata backbone still runs), `X_API_KEY` (X verify + X connector; no key → X verify no-ops, X connector ships disabled).

## 11. File structure

```
src/bellwether/
├── models/figure.py, source.py           # (modify) add discovery columns
├── discovery/
│   ├── __init__.py
│   ├── contracts.py    # Discoverer, WikidataClient, WebSearch, XVerifier protocols; DTOs; DiscoveryError
│   ├── wikidata.py     # WikidataClient adapter + build_wikidata()
│   ├── websearch.py    # WebSearch protocol impl (Tavily) + build_web_search() + registry
│   ├── xverify.py      # XVerifier adapter + build_x_verifier()
│   ├── discoverer.py   # DSPy Discovery module + build_discoverer()
│   ├── connectors.py   # pure Wikidata/candidate -> (connector_type, config) mapping + feed auto-discovery
│   ├── verify.py       # pure score_binding(signals) -> (confidence, verified)
│   └── pipeline.py     # run_discovery(session, figure, *, clients) orchestration
├── connectors/x.py     # (new) X ingestion connector, registered in Plan 2's registry; disabled w/o key
├── queue.py            # (modify) claim_pending_figure / reclaim_stale_figures (SKIP LOCKED on discovery_status)
├── worker.py           # (modify) make_discovery_stage + CLI choice "discovery"; stays Wikidata/Tavily/DSPy-free
├── api/watchlist.py    # (modify) POST /figures discover flag; POST /figures/{id}/discover; extended reads
├── api/discovery.py    # (new) GET /discovery/queue, POST /discovery/{source_id}
├── api/schemas.py      # (modify) discovery status/queue schemas
├── api/app.py          # (modify) include discovery router
└── config.py           # (modify) discovery_model, discovery_confidence_threshold
migrations/versions/    # ONE migration: figures + sources discovery columns
```

## 12. Testing

- **Pure units:** `score_binding` (each signal → confidence → gate, incl. the 1-authority-pending / 2-authority-active boundary); connector mapping + feed auto-discovery; disambiguation/gap-fill via `DummyLM`.
- **`run_discovery`** with stub Wikidata/WebSearch/XVerifier/Discoverer → asserts the right bindings + `active`/`pending_review` split; ambiguous-identity and no-match paths; dedup/re-run idempotency (no duplicate sources).
- **Discovery worker stage** on real Postgres: claim a `pending` figure → pipeline with stubs → sources written + `discovery_status=done`; the `failed`/retry path.
- **X connector** with a stubbed X client → fetch → RawItems; disabled without a key.
- **Review API** on real Postgres, owner-scoped; a **live commit-path smoke** of `POST /discovery/{id}` before merge (write-endpoint lesson).
- **Live verification** (manual, not in suite): real Wikidata + Tavily discovery for a known figure end-to-end; the X connector against the live X API when a key is present.

## 13. Deferred with intent

- Dedicated YouTube connector (RSS feed covers it now).
- **Optimizing** the Discovery module (Plan 6 records confirm/reject labels via the source lifecycle; the flywheel is a later symmetric application of Plan 5).
- `discovery_labels` table (until we optimize Discovery).
- Scheduled/automatic re-discovery; additional Wikidata properties / source types.
