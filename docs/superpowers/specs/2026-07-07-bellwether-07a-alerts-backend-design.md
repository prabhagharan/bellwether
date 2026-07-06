# bellwether Plan 7a — Alerts & Query Backend — Design Spec

**Date:** 2026-07-07
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Parent spec:** `2026-07-04-bellwether-design.md` (§13 API, §14 Alerts)
**Builds on:** Plans 3–6 (DSPy Extract + the SKIP-LOCKED worker harness; `impacts` from Plan 4; the Plan-5 Track-A/Track-B firewall + minimal `trackb/report.py`; Plan 6's certifi SSL context + owner-scoped API patterns).
**Note:** Plan 7 was split — this is **7a (backend)**. The Next.js frontend is a separate follow-on plan **7b**.

---

## 1. Goal

A rule engine that watches the pipeline's extraction-time signals and, on a match, dispatches to a per-rule **webhook** (Slack/Discord) and an **SSE live feed** — plus the deferred **Track-B per-figure impact leaderboard** and the **query APIs** a dashboard reads. All Python/FastAPI, consistent with Plans 1–6.

**In scope:** a decoupled `alert` worker stage; `alert_rules` + `alerts` tables + an `alert_status` column on `extractions`; the deterministic condition engine; the `Notifier` webhook seam; the SSE `/stream` endpoint; the Track-B leaderboard; `/signals` + `/impacts` query APIs; alert-rule CRUD; CORS for the future frontend.

**Out of scope (deferred with intent):** impact-time alerts (v1 fires on the extracted signal only); webhook auto-retry/backoff; other channels (email); resolved-symbol conditions (resolution happens after extraction); and the **Next.js frontend (Plan 7b)**.

## 2. Hard invariants

- **Alerting never blocks the pipeline.** It's a separate worker stage claiming extractions off an `alert_status` column, independent of the `statements.status` resolve/measure flow. A slow/failing webhook affects nothing upstream.
- **Idempotent, fire-once.** An extraction is alert-evaluated exactly once (the `alert_status` claim guards retries), and `alerts` is unique on `(extraction_id, rule_id)` — a retry never double-fires.
- **A webhook failure is recorded, not retried into the pipeline.** The `alerts` row (the match) is persisted and appears on the live feed regardless; only `webhook_status` reflects the dispatch outcome. `alert_status` still → `done`.
- **Firewall untouched.** The rule engine reads extractions (the semantic signal), never impacts. The Track-B leaderboard lives in `trackb/` and reads impacts+extractions+figures — it is Track-B *reporting*, distinct from the `eval/` Track-A golden-label metric; the Plan-5 firewall test still holds.
- **`worker.py` stays clean.** The alert stage imports the engine + `Notifier` contract; the `build_notifier` import is function-local in `_build_stage`. No urllib/webhook code at module top (the Plan-6 discipline).
- **Owner-scoping throughout.** Rules, alerts, the SSE stream, and every query API are scoped by the extraction's figure owner. `alert_rules`/`alerts` carry `owner_id`.
- **Provider-agnostic / secrets policy:** per the user's decision, webhook URLs are **per-rule in the DB** (`alert_rules.webhook_url`), so there is **no new env credential**. New `Settings` are behavior knobs only (`cors_origins`, `sse_poll_interval_seconds`).
- **Deterministic conditions.** No DSL/`eval` — `condition` is a typed JSON structure evaluated by a pure function.
- **Tests: real Postgres, no live network.** The `Notifier` is stubbed; webhooks + SSE are live-verified manually (the market/discovery discipline). Write endpoints get a live commit-path smoke before merge.

## 3. Architecture & flow

A new **`alert` worker stage**, decoupled from the resolve/measure path.

```
Extract writes Extraction (alert_status="pending")
        │  (independent of statements.status — resolve/measure proceed in parallel)
  alert worker claims a pending extraction (claim_pending_extraction, SKIP LOCKED)
        │
   load figure + owner; fetch the owner's ENABLED alert_rules
        │
   for each rule where matches(rule.condition, extraction, figure_id):
        insert alerts row (extraction_id, rule_id, owner_id, payload,
                           webhook_status = "pending" if rule.webhook_url else "skipped")
        │
   dispatch: for each new alerts row with a rule webhook_url ->
        Notifier.notify(webhook_url, payload) -> webhook_status = sent|failed, sent_at
        │
   set extraction.alert_status = "done"; commit
        │
   SSE /stream (API process) polls the alerts table -> streams new owner alerts to the dashboard
```

The `alerts` table is the single source of truth for both dispatch paths: the **webhook** is pushed by the worker; the **SSE feed** is polled from `alerts` by the API — so they work across the separate worker and API processes with no in-memory pub/sub.

## 4. Data model

### 4.1 `extractions` (add columns)
| Column | Type | Notes |
|---|---|---|
| `alert_status` | str | `pending` → `alerting` → `done` (default `"pending"`); the alert worker's claim column |
| `alert_claimed_at` | tz datetime, nullable | SKIP-LOCKED claim stamp |

**Migration backfills existing rows to `done`** (server_default `"pending"` for new rows) so enabling the alert worker doesn't retroactively fire on historical extractions.

### 4.2 `alert_rules` (new)
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `owner_id` | FK→users.id, nullable | single-user; scopes evaluation |
| `name` | str | label |
| `condition` | JSONB | `{min_confidence?, min_magnitude?, directions?, figure_ids?}` (see §5) |
| `webhook_url` | str, nullable | per-rule target; null = SSE-only rule |
| `enabled` | bool | default true |
| `created_at` | tz datetime | |

### 4.3 `alerts` (new — the match log)
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `extraction_id` | FK→extractions.id (CASCADE, indexed) | |
| `rule_id` | FK→alert_rules.id (**SET NULL**) | log survives a deleted rule |
| `owner_id` | FK→users.id, nullable | denormalized — owner-scoped SSE queries + survives rule deletion |
| `payload` | JSONB | alert content (figure name, direction, magnitude, confidence, entities, statement snippet + url) |
| `webhook_status` | str | `pending`/`sent`/`failed`/`skipped` (default `pending`) |
| `sent_at` | tz datetime, nullable | webhook delivery time |
| `created_at` | tz datetime | streamed order key for SSE |

**Unique `(extraction_id, rule_id)`** — belt-and-suspenders against double-fire on top of the claim guard.

## 5. Condition engine (pure, deterministic)

`alerts/rules.py::matches(condition: dict, extraction, figure_id: int) -> bool`. All present fields are **ANDed**; an absent field is unconstrained (empty `{}` matches everything). No `eval`/DSL.
- `min_confidence` → `extraction.confidence >= min_confidence`
- `min_magnitude` → `rank(extraction.magnitude) >= rank(min_magnitude)`, ordinal `none<small<moderate<large`
- `directions` (list) → `extraction.direction in directions`
- `figure_ids` (list) → `figure_id in figure_ids`

All fields are available at extraction time (no resolved symbols — resolution is later). The `condition` shape is pydantic-validated on write (§10).

## 6. Alert stage, dispatch & the Notifier seam

- **`make_alert_stage(notifier) -> Stage`**: `claim_next = claim_pending_extraction`; `process` loads the extraction's figure+owner, evaluates the owner's enabled rules via `matches`, inserts an `alerts` row per match (payload from the extraction), dispatches each row with a `webhook_url` via `notifier.notify(...)` → sets `webhook_status`/`sent_at`, then `alert_status="done"`, commit. `reclaim` resets stale `alerting` rows.
- **`alerts/engine.py::evaluate_extraction(session, extraction, notifier)`** holds the match+insert+dispatch logic (testable directly); the stage is a thin wrapper.
- **Notifier seam** (`alerts/contracts.py` + `alerts/notifier.py`): `Notifier` protocol `notify(webhook_url: str, payload: dict) -> NotifyOutcome(ok: bool)`; `WebhookNotifier` POSTs the payload (urllib + `bellwether.ssl_ctx.SSL_CONTEXT`), never raises (failure → `ok=False`); `build_notifier()`. To work with both Slack and Discord incoming webhooks, the body sends **both** `text` and `content` keys (each platform ignores the other's). Stubbed in tests; live-verified.
- A webhook failure records `webhook_status="failed"` and moves on — the extraction still completes and the match is on the live feed. Auto-retry deferred.

## 7. SSE live feed — `GET /stream`

Owner-scoped, CORS-enabled. `EventSource` can't set headers, so the JWT arrives via `?token=` (validated identically to the `Authorization` header). On connect, record the current max `alerts.id` for the owner; then poll every `sse_poll_interval_seconds` (default 2.0) for that owner's rows with `id >` last-seen, emitting each as an SSE `event: alert\ndata: <payload json>`; send a heartbeat comment (`: ping`) every ~15s to hold the connection. `StreamingResponse`, `media_type="text/event-stream"`. DB-polling → works across the worker (writes) and API (streams) processes.

## 8. Track-B leaderboard + query APIs

All owner-scoped, on top of the existing `/statements`, `/watchlist`, `/review`, `/discovery` endpoints.
- **`GET /leaderboard`** (`trackb/report.py`, expanded): per figure over `measured` impacts — `n`, `avg_pct_move` (signed), `avg_abs_pct_move`, `directional_hit_rate` (fraction where `sign(pct_move)` matched the extraction's predicted `direction`). Sorted by `avg_abs_pct_move` desc. Track-B reporting; reads extractions+impacts+figures; the `eval/` Track-A metric is untouched (firewall holds).
- **`GET /signals`** — the joined signal feed (extraction + figure, optional resolution/impact) with filters `figure_id`/`direction`/`min_confidence`/date-range/`limit`. The live-feed page's backing data.
- **`GET /impacts`** — measured impacts with filters `figure_id`/`symbol`/`window`/date-range. The impact-view backing data.

## 9. Alert-rule CRUD + CORS

- **`/alert_rules`** (owner-scoped): `POST` (create: name, condition, webhook_url, enabled), `GET` (list), `PATCH /{id}` (edit condition/webhook_url/enabled), `DELETE /{id}`. `condition` is pydantic-validated (typed optional fields; unknown keys rejected).
- **CORS:** `CORSMiddleware` in `create_app()` for `settings.cors_origins`, required for the Next.js app to call the API + open the SSE stream.

## 10. Configuration

`Settings` (behavior knobs only — no new credentials): `cors_origins: list[str] = []` (frontend origin(s)), `sse_poll_interval_seconds: float = 2.0`, `alert_webhook_timeout_seconds: float = 10.0`.

## 11. File structure

```
src/bellwether/
├── models/ alert_rule.py, alert.py (new); extraction.py (+ alert_status, alert_claimed_at)
├── alerts/
│   ├── __init__.py
│   ├── contracts.py   # Notifier protocol, NotifyOutcome
│   ├── rules.py       # matches(condition, extraction, figure_id)  [pure]
│   ├── notifier.py    # WebhookNotifier + build_notifier()
│   └── engine.py      # evaluate_extraction(session, extraction, notifier)
├── trackb/report.py   # (expand) leaderboard aggregation
├── queue.py           # (add) claim_pending_extraction / reclaim_stale_alerting (resets extractions stuck in alert_status="alerting")
├── worker.py          # (add) make_alert_stage + CLI "alert"; stays clean (function-local build_notifier)
├── api/
│   ├── alert_rules.py # rule CRUD
│   ├── stream.py      # SSE /stream
│   ├── feed.py        # /signals, /impacts, /leaderboard
│   ├── app.py         # (add) CORSMiddleware + include routers
│   └── schemas.py     # (add) AlertRuleCreate/Read, AlertCondition, SignalRead, ImpactRead, LeaderboardRow
└── config.py          # (add) cors_origins, sse_poll_interval_seconds, alert_webhook_timeout_seconds
migrations/            # ONE migration: extractions alert cols (backfill done) + alert_rules + alerts
```

## 12. Testing

- **Pure:** `matches` (each field, ordinal magnitude, ANDing, empty-matches-all); the leaderboard/hit-rate math.
- **Queue:** `claim_pending_extraction` + reclaim (SKIP LOCKED).
- **Alert stage / engine** with a stub Notifier (real PG): matching vs non-matching rules → correct `alerts` rows + `alert_status=done` + `webhook_status`; `(extraction_id, rule_id)` unique prevents double-fire; a failed webhook is recorded but the extraction still completes.
- **SSE:** the event generator yields an owner's new alerts; `?token=` auth; owner-scoping.
- **CRUD + `/signals`/`/impacts`/`/leaderboard`:** owner-scoped, correct filters/aggregation.
- **Live (manual, pre-merge):** a commit-path smoke of the rule-CRUD write endpoints; a real webhook POST (test Slack/Discord URL); the SSE stream emitting a real alert end-to-end.

## 13. Deferred with intent

- Impact-time alerts (v1 is extraction-time).
- Webhook auto-retry/backoff.
- Additional channels (email, etc.).
- Resolved-symbol conditions.
- **The Next.js frontend — Plan 7b.**
