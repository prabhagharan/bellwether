# bellwether — Design Spec

**Date:** 2026-07-04
**Status:** Approved for implementation planning
**Author:** Prabhagharan

---

## 1. Purpose

bellwether is a **single-user research/monitoring system** that watches high-influence
figures a user chooses (politicians, executives, central banks, institutions — anyone),
ingests their **real, public statements**, extracts a structured market signal from each,
and measures the statement's **actual** market impact.

It exists to answer two deliberately separated questions:

1. **Can we read these people accurately?** (semantic accuracy)
2. **Do their statements actually move markets?** (measured impact)

It is a research/monitoring instrument, **not** a trading bot. It only ever reads what
was genuinely said.

## 2. Hard boundaries (non-negotiable, enforced in code)

- **Read-only ingestion.** Connectors only fetch already-published content. Nothing is
  ever synthesized.
- **No generation / impersonation / voice** of any figure. The system never produces,
  fabricates, clones, or paraphrases-as-original anyone's statements.
- **No trading execution.** No order/execution APIs, no live-trading claims. Impact is
  *measurement only*.
- **Provenance always attached.** Every statement carries a source URL and a provenance
  level (`primary` vs `reported`).
- **Verbatim-substring guard.** Every extracted `evidence_quote` must be a literal
  substring of the original statement text, checked in code. Non-substring extractions
  are rejected. This is the structural anti-fabrication guarantee.

## 3. Scope

**In:** consuming genuine public statements; automatic verified source discovery;
market-relevance detection; structured signal extraction; entity→symbol resolution;
event-study impact measurement; semantic-accuracy evaluation and prompt optimization;
API + dashboard + alerts; single-user auth.

**Out (v1):** trading/execution; any statement generation; full multi-user auth flows;
abnormal-return benchmarking; paid tick-data; LLM-as-judge bulk labeling;
Extract-as-multi-step-agent; X ingestion enabled by default.

## 4. Architecture overview

```
                 ┌──────────── user watchlist (API / dashboard) ────────────┐
                 │  add figure (auto-discover sources) + manual add sources  │
                 └───────────────────────────┬──────────────────────────────┘
                                             │
  Ingest ──▶ Detect ──▶ Extract ──▶ Resolve ──▶ Measure ──▶ API / Dashboard / Alerts
 (connectors) (LLM,     (LLM,       (entity→    (event
              optimized) optimized)  symbol)     study)
                 │           │
                 │           └── golden set ─▶ manual DSPy compile ─▶ versioned prompt
                 └── relevance labels ─────────┘   (champion/challenger promotion)

   EVALUATE  ── Track A: semantic accuracy (human golden labels) ──▶ drives DSPy
             ── Track B: event-study impact (realized moves) ──▶ reported separately
             FIREWALL: Track B never feeds Track A
```

**Orchestration:** each pipeline stage is an independent, restartable, idempotent
**worker process**. Stages coordinate through **Postgres** used as both the shared store
and a job queue: a worker claims the next row with
`SELECT … FOR UPDATE SKIP LOCKED`, does its work, and advances a `status` column.

## 5. Pipeline stages

| Stage | LLM? | Input → Output |
|---|---|---|
| **Ingest** | No | source binding → normalized `statements` (precise `published_at`, provenance, URL, dedup key) |
| **Detect** | **Yes (DSPy, cheap model, optimized)** | statement → `{is_relevant, score}`; irrelevant statements stop here |
| **Extract** | **Yes (DSPy, optimized model)** | statement → `{entities, direction, magnitude, confidence, evidence_quote}` |
| **Resolve** | No | entities → tradable symbols (company→ticker, sector→ETF, crypto→pair); unmappable → flagged non-measurable |
| **Measure** | No | resolved signal → event-study impact (price/volume/volatility in windows) |

### 5.1 Ingest
Connectors implement one interface (`fetch() → [RawItem]`). Built-in connectors:

- **Official / RSS / press** — central-bank speech feeds, gov press pages, SEC EDGAR, IR
  pages. `primary`.
- **YouTube transcripts** — press conferences, earnings calls, interviews. `primary`.
- **News** — a **name/alias-scoped query** over a news provider (not a fixed handle).
  Everything returned is `reported` and must link a primary source.
- **X / Twitter** — verified-account posts. **Ships disabled** until an API key is set
  (official API is paid/rate-limited; scraping violates ToS).

Precise timestamps are load-bearing: the impact measurement window is anchored to
`published_at`. Dedup by connector-specific `external_id`.

### 5.2 Detect
A DSPy classification module on a **cheap/fast model**, run on **every** ingested
statement, gating the expensive Extract step. Optimized against `relevance_labels`
harvested as a **byproduct** of the review loop (kept golden extractions = positives;
"not actually relevant" rejections = negatives) — no dedicated labeling task.

### 5.3 Extract
The core DSPy module on the **optimized model**. Produces the structured signal.
`evidence_quote` **must** be a verbatim substring (enforced). Optimized against the
extraction golden set.

### 5.4 Resolve
Deterministic entity→symbol resolution via a curated map (company→ticker, sector→ETF,
crypto→pair). Ambiguous/unmappable entities are stored but flagged non-measurable.
(Optional future: LLM disambiguation fallback for hard cases.)

### 5.5 Measure (the oracle)
Runs on a **delay**, after the event window elapses. Pulls price/volume/volatility for
each resolved symbol around `published_at` and computes the realized move at configurable
windows (default **5m / 1h / 1d**) vs a baseline. Market-data adapter: **yfinance**
(equities/index/FX/commodities) + **CoinGecko** (crypto), upgradeable to a paid tick
source later. Free-data granularity is coarse, so sub-minute windows are not offered.

## 6. LLM layer — DSPy only

- **Single framework: DSPy**, model-agnostic via LiteLLM. No LangChain/LangGraph —
  every LLM stage is a single call, so orchestration frameworks add nothing here.
- **DSPy modules:** Detect (cheap `detect_model`) and Extract (optimized
  `extract_model`), both swappable by config; plus a **Discovery** module for source
  discovery (name disambiguation + source gap-fill), **un-optimized initially** and
  optimizable later from review-queue byproduct labels. All model-agnostic; not bound to
  any one provider.
- **Optimization flywheel:** review-and-correct produces golden sets; a **manual**
  "optimize now" job compiles a new prompt version. A new version is **promoted only if
  it beats the current champion on a frozen held-out set** (champion/challenger).
  Versions are stored artifacts → instant rollback.
- **Train/held-out split:** the optimizer only sees train; accuracy is reported on a
  frozen held-out set so version comparisons stay honest.

## 7. Evaluation — two firewalled tracks

- **Track A — semantic accuracy.** Extraction/detection vs held-out human golden labels.
  This metric — and *only* this — drives DSPy optimization.
- **Track B — event-study impact.** Realized market moves, aggregated and reported
  **separately** (e.g. "Musk's TSLA statements move TSLA X% within 1h").
- **Firewall:** market outcomes never enter Track A. A faithful reading of a statement
  the market ignored is still correct. A test asserts market data never reaches Track A's
  metric path.

## 8. Source discovery & configuration

When a user adds a figure (by name), a **discovery** step establishes verified source
bindings automatically; manual add is always available on top.

- **Automatic (default):** the **backbone is deterministic** — a structured **Wikidata**
  lookup (`P856` official website, `P2002` X username, `P2397` YouTube channel) plus
  aliases auto-populated from Wikidata (used by the news query and dedup). A **DSPy
  Discovery module (LLM)** handles the fuzzy parts: **disambiguating** which entity the
  typed name refers to, and **gap-filling** sources Wikidata lacks (via web search),
  proposed as *candidates* that still pass verification. Un-optimized initially; the
  review-queue confirm/reject actions are a byproduct label source to optimize it later
  (symmetric with Detect).
- **Auto-verify by cross-reference:** a binding is trusted only when independent
  authorities agree (Wikidata + X verified status + official-domain link). Each binding
  gets a `discovery_confidence`.
- **Auto-enable confident bindings; queue the ambiguous tail.** High-confidence bindings
  activate automatically; low-confidence/conflicting ones land in a **"needs review"**
  (`pending_review`) state — the safety valve that preserves the provenance guarantee.
- **Manual add:** the user can directly add a source they know (handle, feed, channel,
  news query). Tagged `origin: manual`, still carries verification/provenance.

Every binding: `{connector_type, config, provenance, verified, origin, discovery_confidence, status, enabled}`.

## 9. Preferences & configuration

- **Runtime preferences → Postgres, API/dashboard-editable:** watchlist (`figures`,
  `sources`), `alert_rules`, and `settings` (event-study windows, relevance threshold,
  `detect_model`/`extract_model`, poll intervals, alert thresholds).
- **Secrets & infra → env vars via `pydantic-settings`, never in DB or API:** LLM
  provider keys, X API key, webhook secret URLs, Postgres URL, JWT signing secret/expiry,
  `ADMIN_USERNAME`/`ADMIN_PASSWORD`.
- **Rule of thumb:** behavior-changing knobs → DB; credentials/deployment facts → env.

## 10. Authentication

- **JWT** via `PyJWT` + `passlib[bcrypt]` + FastAPI `OAuth2PasswordBearer`.
- **User creation — env seeding:** on startup, if the `users` table is empty, create the
  admin from `ADMIN_USERNAME`/`ADMIN_PASSWORD` (bcrypt-hashed).
- `POST /auth/token` (username/password → access token); `get_current_user` dependency
  guards routes; the Next.js app stores the JWT and sends `Authorization: Bearer …`.
- No open public registration in v1.

## 11. Tenancy

- **Single-user (single-tenant) v1**, but **multi-user-ready**: every user-owned table
  carries a nullable `owner_id`.
- **Future multi-user model (deferred):** a **shared corpus** (`statements`,
  `detections`, `extractions`, `impacts` — facts fetched/processed once) plus **per-user
  subscriptions and preferences** (a `user_figures` join, per-user `alert_rules`,
  `settings`). Users subscribe to a shared figure catalog; the expensive
  ingest/extract/measure work is not duplicated per user.

## 12. Data model (Postgres)

Tables (user-owned ones carry nullable `owner_id`):

- `users`
- `figures` (name, type, aliases, `owner_id`)
- `sources` (figure_id, connector_type, config JSONB, provenance, verified, origin,
  discovery_confidence, status, enabled, poll_interval, `owner_id`)
- `statements` (figure_id, source_id, external_id, text, url, provenance, published_at,
  ingested_at, status)
- `detections` (statement_id, is_relevant, score, model, version)
- `extractions` (statement_id, entities JSONB, direction, magnitude, confidence,
  evidence_quote, model, version)
- `resolutions` (extraction_id, entity, symbol, asset_class, measurable)
- `impacts` (resolution/extraction_id, symbol, window, price_t0, price_after, pct_move,
  volume_spike, measured_at)
- `relevance_labels` (statement_id, is_relevant, source, split)
- `extraction_labels` (statement_id, gold fields, source, split)
- `eval_runs` (track, metric, score, dspy_program_id, created_at)
- `dspy_programs` (module, version, artifact, holdout_score, is_champion, created_at)
- `alert_rules` (condition, channel, enabled, `owner_id`)
- `settings` (key/value or typed singleton, `owner_id`)
- `alerts` (extraction_id, rule_id, channel, payload, sent_at)

JSONB for flexible fields (entities, source config, artifacts).

## 13. API & frontend

- **API-first FastAPI**, OpenAPI spec is the integration surface. Endpoints: auth;
  watchlist CRUD (figures/sources, trigger discovery, review queue); query
  statements/extractions/impacts with filters (figure/theme/asset/date/direction);
  per-figure impact leaderboard; review-and-correct (golden set); optimize trigger +
  version history; alert rules; `/stream` (SSE) live feed. **CORS** enabled for the app
  origin.
- **Frontend:** minimal **Next.js** app on the REST API, with a **typed client generated
  from the OpenAPI spec**. Pages: live feed; watchlist + source discovery/review;
  review-and-correct; impact view; eval/version panel; alert-rule config.

## 14. Alerts

Rule engine evaluates each measured/extracted signal against user `alert_rules`
(e.g. `confidence ≥ 0.7 AND magnitude ≥ moderate AND figure ∈ {…}`). Matches dispatch to
**webhook** (Slack/Discord) and the **dashboard live feed** (SSE).

## 15. Tech stack

- **Backend:** Python 3.11+, FastAPI + uvicorn, SQLAlchemy, Postgres (WAL default,
  `FOR UPDATE SKIP LOCKED` queue), plain worker processes, `pydantic-settings`.
- **LLM:** DSPy (model-agnostic via LiteLLM).
- **Market data:** yfinance + CoinGecko behind one adapter.
- **Ingestion:** feedparser, httpx, a YouTube transcript lib; X adapter stubbed/disabled.
- **Discovery:** Wikidata client, web search, X verification.
- **Auth:** PyJWT, passlib[bcrypt].
- **Frontend:** Next.js (TypeScript), OpenAPI-generated client.
- **Tests:** pytest.

## 16. Deployment / runtime

Develop and run on the laptop first, but **container-friendly** (Docker) so moving to an
always-on box (VPS/Pi/home server) later is trivial. A live monitor only catches
statements while it is running; an always-on host is the intended production posture.
Postgres runs as a local Docker container in dev, a managed/hosted instance later.

## 17. Testing strategy

- **Unit:** connector normalization (fixture feeds), resolver, impact math (synthetic
  price series), alert rules, substring/provenance guards, discovery verification logic.
- **LLM stages:** DSPy modules validated on a golden set; deterministic checks on schema
  + evidence-substring.
- **Market adapter:** replayed fixtures — **no live network in tests**.
- **Workers/queue:** claim-once semantics, idempotent reprocessing, dedup.
- **End-to-end:** seeded statement fixture → full pipeline → expected extraction + impact
  rows.
- **Firewall test:** assert market data never enters Track A's metric path.

## 18. Future extensions (deferred)

- Full multi-user: shared-corpus + per-user subscription model, admin user-management
  flows, per-user scoping on all queries.
- Abnormal-return (subtract benchmark/sector) in Measure.
- Paid tick-data source for fine-grained intraday windows.
- LLM-as-judge bulk labeling (validated against the human golden anchor).
- Extract as a multi-step agent (draft → self-critique → refine) — the one place
  LangGraph would pay off.
- X ingestion enabled with an API key.
- LLM disambiguation fallback in Resolve.
