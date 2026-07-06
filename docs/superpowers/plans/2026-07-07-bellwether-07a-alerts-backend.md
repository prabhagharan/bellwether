# bellwether Plan 7a — Alerts & Query Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A decoupled `alert` worker stage that evaluates extraction-time rules and dispatches matches to per-rule webhooks + an SSE live feed, plus the Track-B impact leaderboard and the dashboard query APIs.

**Architecture:** New `alert` worker stage claims extractions off an `alert_status` column (independent of the resolve/measure `statements.status` flow), evaluates the figure-owner's enabled `alert_rules` with a pure deterministic matcher, writes `alerts` rows, and dispatches via a `Notifier` seam. Webhook (worker) and SSE feed (API) both read the persisted `alerts` table — no in-memory pub/sub, so it works across processes. Plus `/leaderboard`, `/signals`, `/impacts` query APIs and CORS.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, Alembic, Postgres, FastAPI (`StreamingResponse` for SSE, `CORSMiddleware`), stdlib `urllib` + Plan-6 `bellwether.ssl_ctx.SSL_CONTEXT`, pytest. Design spec: `docs/superpowers/specs/2026-07-07-bellwether-07a-alerts-backend-design.md`.

## Global Constraints

- Python **3.11+**; SQLAlchemy 2.0 (`Mapped`/`mapped_column`); JSONB via `sqlalchemy.dialects.postgresql`.
- **Decoupled + idempotent:** the alert stage claims `extractions.alert_status` (SKIP LOCKED); an extraction is evaluated once; `alerts` is **unique `(extraction_id, rule_id)`**; a webhook failure is recorded (`webhook_status="failed"`), never re-queues the extraction.
- **Migration backfills existing extractions to `alert_status="done"`** (new rows default `"pending"`) — no retroactive alerting.
- **Firewall untouched:** the alert engine reads extractions, never impacts; the Track-B leaderboard lives in `trackb/` (reads impacts+extractions+figures) and is separate from the `eval/` Track-A metric.
- **`worker.py` stays clean:** `build_notifier` import is function-local in `_build_stage`; no urllib/webhook code at module top.
- **Owner-scoping everywhere:** rules, alerts, SSE, and every query API scoped by the extraction's figure owner. `alert_rules`/`alerts` carry `owner_id`.
- **No new env credentials:** webhook URLs are per-rule in the DB (`alert_rules.webhook_url`). New `Settings` are behavior knobs only.
- **Deterministic conditions:** `condition` is typed JSON evaluated by a pure function — no `eval`/DSL.
- **Impact→figure join:** `Impact → Resolution(resolution_id) → Extraction(extraction_id) → Statement(statement_id) → Figure(figure_id)`; owner = `Figure.owner_id`.
- **Tests: real Postgres, no live network.** The `Notifier` is stubbed; webhooks/SSE live-verified manually. Write endpoints get a live commit-path smoke pre-merge. Use `.venv/bin/python -m pytest …` / `.venv/bin/alembic …`; Postgres via `docker compose up -d`; a yfinance transitive import adds ~15-30s to a full run. If a full-suite run **hangs/times out**, report it — do NOT restart Postgres from a subagent (`docker compose restart db` clears a wedge; controller-only).

## File Structure

```
src/bellwether/
├── models/ alert_rule.py, alert.py (new); extraction.py (+ alert_status, alert_claimed_at)
├── alerts/
│   ├── __init__.py
│   ├── contracts.py   # Notifier protocol, NotifyOutcome
│   ├── rules.py       # matches(condition, extraction, figure_id)  [pure]
│   ├── notifier.py    # WebhookNotifier + build_notifier()
│   └── engine.py      # evaluate_extraction(session, extraction, notifier)
├── trackb/report.py   # (add) leaderboard_by_figure
├── queue.py           # (add) claim_pending_extraction / reclaim_stale_alerting
├── worker.py          # (add) make_alert_stage + CLI "alert"
├── api/
│   ├── alert_rules.py # rule CRUD
│   ├── stream.py      # SSE /stream
│   ├── feed.py        # /signals, /impacts, /leaderboard
│   ├── app.py         # (add) CORSMiddleware + include routers
│   └── schemas.py     # (add) schemas
└── config.py          # (add) cors_origins, sse_poll_interval_seconds, alert_webhook_timeout_seconds
migrations/            # ONE migration
```

---

### Task 1: Models + migration

**Files:**
- Create: `src/bellwether/models/alert_rule.py`, `src/bellwether/models/alert.py`
- Modify: `src/bellwether/models/extraction.py`, `src/bellwether/models/__init__.py`
- Create: the generated migration
- Test: `tests/test_models_alerts.py`

**Interfaces:**
- Produces: `AlertRule` (`alert_rules`: id, `owner_id` FK→users.id nullable, `name` str, `condition` JSONB default dict, `webhook_url` str|null, `enabled` bool default True, `created_at`); `Alert` (`alerts`: id, `extraction_id` FK→extractions.id CASCADE indexed, `rule_id` FK→alert_rules.id **SET NULL** nullable, `owner_id` FK→users.id nullable, `payload` JSONB default dict, `webhook_status` str default "pending", `sent_at` tz datetime|null, `created_at`; **unique `(extraction_id, rule_id)`** = `uq_alerts_extraction_rule`). `Extraction` gains `alert_status` str default "pending", `alert_claimed_at` tz datetime|null.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_alerts.py
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.models.extraction import Extraction


def test_alert_rule_columns():
    c = set(AlertRule.__table__.columns.keys())
    assert {"id", "owner_id", "name", "condition", "webhook_url", "enabled", "created_at"} <= c


def test_alert_columns_and_unique():
    c = set(Alert.__table__.columns.keys())
    assert {"id", "extraction_id", "rule_id", "owner_id", "payload", "webhook_status", "sent_at", "created_at"} <= c
    uniques = [u for u in Alert.__table__.constraints if u.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"extraction_id", "rule_id"} for u in uniques)


def test_extraction_alert_columns():
    c = set(Extraction.__table__.columns.keys())
    assert {"alert_status", "alert_claimed_at"} <= c
    assert Extraction.__table__.columns["alert_status"].default.arg == "pending"
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_models_alerts.py -v` — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `alert_rule.py`**

```python
# src/bellwether/models/alert_rule.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Write `alert.py`**

```python
# src/bellwether/models/alert.py
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("extraction_id", "rule_id", name="uq_alerts_extraction_rule"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    webhook_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: Add the Extraction columns + register**

In `src/bellwether/models/extraction.py`, add after `version` (import `Boolean`? no — need nothing new beyond existing `DateTime`/`String`):
```python
    alert_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    alert_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```
In `src/bellwether/models/__init__.py`, add `from bellwether.models.alert_rule import AlertRule` and `from bellwether.models.alert import Alert` and add both to `__all__`.

- [ ] **Step 6: Run the model test → pass**

Run: `.venv/bin/python -m pytest tests/test_models_alerts.py -v` — PASS (3 tests).

- [ ] **Step 7: Generate + apply migration (with backfill)**

Run: `.venv/bin/alembic revision --autogenerate -m "alert_rules + alerts + extraction alert_status"`
Open the generated file. Confirm it creates `alert_rules` + `alerts` (with the CASCADE / SET NULL FKs, the `uq_alerts_extraction_rule` unique, the `alerts.extraction_id` index) and adds the two `extractions` columns. **Add `server_default` for `alert_status` and a backfill** — the `add_column` for `alert_status` must be `nullable=False, server_default="pending"`, and append a backfill so existing rows don't retro-fire:
```python
    op.execute("UPDATE extractions SET alert_status = 'done'")
```
(Place the `op.execute` after the `add_column` calls in `upgrade()`.) Then:
```bash
.venv/bin/alembic upgrade head
```
Confirm `.venv/bin/alembic heads` shows one head.

- [ ] **Step 8: Full suite + commit**

Run: `.venv/bin/python -m pytest -q` — all green.
```bash
git add src/bellwether/models tests/test_models_alerts.py migrations
git commit -m "feat: alert_rules + alerts models + extraction alert_status + migration (backfill done)"
```

---

### Task 2: Config

**Files:**
- Modify: `src/bellwether/config.py`, `.env.example`
- Test: `tests/test_config_alerts.py`

**Interfaces:**
- Produces on `Settings`: `cors_origins: list[str] = []`, `sse_poll_interval_seconds: float = 2.0`, `alert_webhook_timeout_seconds: float = 10.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_alerts.py
from bellwether.config import Settings


def test_alert_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.cors_origins == []
    assert s.sse_poll_interval_seconds == 2.0
    assert s.alert_webhook_timeout_seconds == 10.0
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_config_alerts.py -v` — FAIL.

- [ ] **Step 3: Add settings**

In `src/bellwether/config.py`, after the Plan-6 settings, add:
```python
    cors_origins: list[str] = []
    sse_poll_interval_seconds: float = 2.0
    alert_webhook_timeout_seconds: float = 10.0
```

- [ ] **Step 4: Document `.env.example`**

Append:
```bash
# --- Alerts & query backend (Plan 7a) ---
# CORS_ORIGINS=["http://localhost:3000"]   # frontend origin(s) allowed to call the API + SSE
# SSE_POLL_INTERVAL_SECONDS=2.0            # /stream live-feed poll cadence
# ALERT_WEBHOOK_TIMEOUT_SECONDS=10.0
# (webhook URLs are per-rule in the DB, not env)
```

- [ ] **Step 5: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/test_config_alerts.py -v` — PASS.
```bash
git add src/bellwether/config.py .env.example tests/test_config_alerts.py
git commit -m "feat: alerts config (cors_origins, sse poll, webhook timeout)"
```

---

### Task 3: Condition engine (pure)

**Files:**
- Create: `src/bellwether/alerts/__init__.py` (empty), `src/bellwether/alerts/rules.py`
- Test: `tests/alerts/__init__.py` (empty), `tests/alerts/test_rules.py`

**Interfaces:**
- Produces: `matches(condition: dict, extraction, figure_id: int) -> bool`. `extraction` is any object with `.confidence` (float), `.magnitude` (str), `.direction` (str). All present condition fields ANDed; absent = unconstrained; `{}` matches all. Magnitude ordinal `none<small<moderate<large`.

- [ ] **Step 1: Write the failing test**

```python
# tests/alerts/test_rules.py
from dataclasses import dataclass
from bellwether.alerts.rules import matches


@dataclass
class Ex:
    confidence: float
    magnitude: str
    direction: str


def test_empty_condition_matches_all():
    assert matches({}, Ex(0.1, "none", "neutral"), 5) is True


def test_min_confidence():
    e = Ex(0.6, "large", "up")
    assert matches({"min_confidence": 0.7}, e, 1) is False
    assert matches({"min_confidence": 0.5}, e, 1) is True


def test_min_magnitude_ordinal():
    assert matches({"min_magnitude": "moderate"}, Ex(0.9, "small", "up"), 1) is False
    assert matches({"min_magnitude": "moderate"}, Ex(0.9, "large", "up"), 1) is True


def test_directions_and_figures():
    e = Ex(0.9, "large", "up")
    assert matches({"directions": ["down"]}, e, 1) is False
    assert matches({"directions": ["up", "down"]}, e, 1) is True
    assert matches({"figure_ids": [2, 3]}, e, 1) is False
    assert matches({"figure_ids": [1, 2]}, e, 1) is True


def test_all_anded():
    e = Ex(0.9, "large", "up")
    assert matches({"min_confidence": 0.7, "min_magnitude": "moderate",
                    "directions": ["up"], "figure_ids": [1]}, e, 1) is True
    assert matches({"min_confidence": 0.7, "directions": ["down"]}, e, 1) is False
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/alerts/test_rules.py -v` — FAIL.

- [ ] **Step 3: Write `rules.py`**

Create empty `src/bellwether/alerts/__init__.py` and `tests/alerts/__init__.py`, then:
```python
# src/bellwether/alerts/rules.py
_MAGNITUDE_RANK = {"none": 0, "small": 1, "moderate": 2, "large": 3}


def matches(condition: dict, extraction, figure_id: int) -> bool:
    min_conf = condition.get("min_confidence")
    if min_conf is not None and extraction.confidence < min_conf:
        return False
    min_mag = condition.get("min_magnitude")
    if min_mag is not None and _MAGNITUDE_RANK.get(extraction.magnitude, -1) < _MAGNITUDE_RANK.get(min_mag, 0):
        return False
    directions = condition.get("directions")
    if directions and extraction.direction not in directions:
        return False
    figure_ids = condition.get("figure_ids")
    if figure_ids and figure_id not in figure_ids:
        return False
    return True
```

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/alerts/test_rules.py -v` — PASS (5 tests).
```bash
git add src/bellwether/alerts/__init__.py src/bellwether/alerts/rules.py tests/alerts
git commit -m "feat: deterministic alert condition matcher (pure)"
```

---

### Task 4: Notifier seam

**Files:**
- Create: `src/bellwether/alerts/contracts.py`, `src/bellwether/alerts/notifier.py`
- Test: `tests/alerts/test_notifier.py`

**Interfaces:**
- Produces: `NotifyOutcome(ok: bool)` frozen dataclass; `Notifier` protocol (`notify(webhook_url: str, payload: dict) -> NotifyOutcome`); `WebhookNotifier` (POSTs `{"text": ..., "content": ...}` built from `payload["text"]`, uses `bellwether.ssl_ctx.SSL_CONTEXT`, never raises → `NotifyOutcome(ok=False)` on error/non-2xx); `build_notifier() -> Notifier`.

- [ ] **Step 1: Write the failing test**

```python
# tests/alerts/test_notifier.py
from bellwether.alerts.contracts import NotifyOutcome, Notifier
from bellwether.alerts.notifier import WebhookNotifier, build_notifier


def test_contract_and_build():
    n = build_notifier()
    assert isinstance(n, Notifier)


def test_stub_notifier_satisfies_protocol():
    class Stub:
        def notify(self, webhook_url, payload): return NotifyOutcome(ok=True)
    assert isinstance(Stub(), Notifier)


def test_webhook_notifier_bad_url_returns_not_ok():
    # unreachable/invalid URL -> caught -> ok=False (never raises)
    out = WebhookNotifier(timeout=1.0).notify("http://127.0.0.1:0/nope", {"text": "hi"})
    assert isinstance(out, NotifyOutcome) and out.ok is False
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/alerts/test_notifier.py -v` — FAIL.

- [ ] **Step 3: Write `contracts.py` + `notifier.py`**

```python
# src/bellwether/alerts/contracts.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NotifyOutcome:
    ok: bool


@runtime_checkable
class Notifier(Protocol):
    def notify(self, webhook_url: str, payload: dict) -> NotifyOutcome: ...
```

```python
# src/bellwether/alerts/notifier.py
import json
import urllib.request
from bellwether.ssl_ctx import SSL_CONTEXT
from bellwether.alerts.contracts import NotifyOutcome, Notifier


class WebhookNotifier:
    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def notify(self, webhook_url: str, payload: dict) -> NotifyOutcome:
        text = payload.get("text") or json.dumps(payload)
        # Slack wants {"text": ...}, Discord wants {"content": ...} — send both; each ignores the other.
        body = json.dumps({"text": text, "content": text}).encode()
        req = urllib.request.Request(webhook_url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=SSL_CONTEXT) as resp:
                return NotifyOutcome(ok=200 <= resp.status < 300)
        except Exception:
            return NotifyOutcome(ok=False)


def build_notifier() -> Notifier:
    from bellwether.config import get_settings
    return WebhookNotifier(get_settings().alert_webhook_timeout_seconds)
```

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/alerts/test_notifier.py -v` — PASS (3 tests).
```bash
git add src/bellwether/alerts/contracts.py src/bellwether/alerts/notifier.py tests/alerts/test_notifier.py
git commit -m "feat: Notifier webhook seam (Slack/Discord dual-key, certifi SSL, never raises)"
```

---

### Task 5: Alert engine

**Files:**
- Create: `src/bellwether/alerts/engine.py`
- Test: `tests/alerts/test_engine.py`

**Interfaces:**
- Consumes: `matches` (Task 3); `Notifier`/`NotifyOutcome`; `AlertRule`/`Alert`/`Extraction`/`Statement`/`Figure` models.
- Produces: `evaluate_extraction(session, extraction, notifier) -> None` — resolve figure+owner; for each of the owner's enabled rules that `matches`, insert an `Alert` (skip if `(extraction_id, rule_id)` already exists), then if the rule has a `webhook_url` call `notifier.notify` and set `webhook_status`/`sent_at`. Builds `payload` = `{figure, figure_id, direction, magnitude, confidence, entities, url, text}` where `text` is a human summary.

- [ ] **Step 1: Write the failing test**

```python
# tests/alerts/test_engine.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.alerts.contracts import NotifyOutcome
from bellwether.alerts.engine import evaluate_extraction


class SentNotifier:
    def __init__(self, ok=True): self.ok, self.calls = ok, []
    def notify(self, webhook_url, payload): self.calls.append((webhook_url, payload)); return NotifyOutcome(ok=self.ok)


def _extraction(db_session, owner_id=None, direction="up", magnitude="large", confidence=0.9):
    f = Figure(name="Fed", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="Rates will rise sharply.", url="http://x/1",
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["Fed"], direction=direction, magnitude=magnitude,
                    confidence=confidence, evidence_quote="Rates will rise", model="m", version="baseline")
    db_session.add(ex); db_session.flush()
    return f, ex


def test_matching_rule_creates_alert_and_dispatches(db_session):
    f, ex = _extraction(db_session)
    db_session.add(AlertRule(owner_id=None, name="strong up", enabled=True, webhook_url="http://hook",
                             condition={"min_confidence": 0.7, "min_magnitude": "moderate", "directions": ["up"]}))
    db_session.flush()
    notifier = SentNotifier(ok=True)
    evaluate_extraction(db_session, ex, notifier)
    db_session.flush()
    alert = db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalar_one()
    assert alert.webhook_status == "sent" and alert.sent_at is not None
    assert alert.payload["figure"] == "Fed" and alert.payload["direction"] == "up"
    assert len(notifier.calls) == 1 and notifier.calls[0][0] == "http://hook"


def test_non_matching_rule_creates_no_alert(db_session):
    f, ex = _extraction(db_session, direction="up")
    db_session.add(AlertRule(owner_id=None, name="downs only", enabled=True, webhook_url=None,
                             condition={"directions": ["down"]}))
    db_session.flush()
    evaluate_extraction(db_session, ex, SentNotifier())
    db_session.flush()
    assert db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().first() is None


def test_failed_webhook_recorded_and_idempotent(db_session):
    f, ex = _extraction(db_session)
    db_session.add(AlertRule(owner_id=None, name="r", enabled=True, webhook_url="http://hook", condition={}))
    db_session.flush()
    evaluate_extraction(db_session, ex, SentNotifier(ok=False))
    db_session.flush()
    a = db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalar_one()
    assert a.webhook_status == "failed"
    # re-run must not create a duplicate (unique extraction_id, rule_id)
    evaluate_extraction(db_session, ex, SentNotifier(ok=True))
    db_session.flush()
    assert len(db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().all()) == 1
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/alerts/test_engine.py -v` — FAIL.

- [ ] **Step 3: Write `engine.py`**

```python
# src/bellwether/alerts/engine.py
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.figure import Figure
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.alerts.rules import matches


def _payload(figure: Figure, statement: Statement, extraction) -> dict:
    text = (f"{figure.name}: {extraction.direction}/{extraction.magnitude} "
            f"(confidence {extraction.confidence:.2f}) — {statement.text[:160]}")
    return {
        "figure": figure.name, "figure_id": figure.id,
        "direction": extraction.direction, "magnitude": extraction.magnitude,
        "confidence": extraction.confidence, "entities": list(extraction.entities),
        "url": statement.url, "text": text,
    }


def evaluate_extraction(session: Session, extraction, notifier) -> None:
    statement = session.get(Statement, extraction.statement_id)
    figure = session.get(Figure, statement.figure_id)
    owner_id = figure.owner_id
    rules = session.execute(
        select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.owner_id.is_(owner_id)
                                if owner_id is None else AlertRule.owner_id == owner_id)
    ).scalars().all()
    for rule in rules:
        if not matches(rule.condition, extraction, figure.id):
            continue
        exists = session.execute(
            select(Alert).where(Alert.extraction_id == extraction.id, Alert.rule_id == rule.id)
        ).scalar_one_or_none()
        if exists is not None:
            continue
        payload = _payload(figure, statement, extraction)
        alert = Alert(extraction_id=extraction.id, rule_id=rule.id, owner_id=owner_id, payload=payload,
                      webhook_status="pending" if rule.webhook_url else "skipped")
        session.add(alert)
        session.flush()
        if rule.webhook_url:
            outcome = notifier.notify(rule.webhook_url, payload)
            alert.webhook_status = "sent" if outcome.ok else "failed"
            alert.sent_at = datetime.now(timezone.utc) if outcome.ok else None
```

- [ ] **Step 4: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/alerts/test_engine.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/alerts/engine.py tests/alerts/test_engine.py
git commit -m "feat: alert engine (evaluate extraction -> match rules -> alerts + dispatch)"
```

---

### Task 6: Queue — claim_pending_extraction + reclaim

**Files:**
- Modify: `src/bellwether/queue.py`
- Test: `tests/test_queue_alerts.py`

**Interfaces:**
- Produces: `claim_pending_extraction(session, to_status="alerting") -> Extraction | None` (FOR UPDATE SKIP LOCKED on `Extraction.alert_status == "pending"`, oldest by id, sets `alert_status=to_status` + `alert_claimed_at=now`, **commits**); `reclaim_stale_alerting(session, in_status, to_status, older_than_seconds) -> int` (resets stuck extractions, **commits**).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_alerts.py
from datetime import datetime, timedelta, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.queue import claim_pending_extraction, reclaim_stale_alerting


def _ext(db_session, alert_status="pending"):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="small", confidence=0.5,
                    evidence_quote="t", model="m", version="baseline", alert_status=alert_status)
    db_session.add(ex); db_session.flush()
    return ex


def test_claim_pending_extraction(db_session):
    ex = _ext(db_session)
    claimed = claim_pending_extraction(db_session)
    assert claimed.id == ex.id and claimed.alert_status == "alerting" and claimed.alert_claimed_at is not None
    assert claim_pending_extraction(db_session) is None


def test_reclaim_stale_alerting(db_session):
    ex = _ext(db_session, alert_status="alerting")
    ex.alert_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=600); db_session.flush()
    n = reclaim_stale_alerting(db_session, "alerting", "pending", 300)
    assert n == 1
    db_session.refresh(ex)
    assert ex.alert_status == "pending" and ex.alert_claimed_at is None
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_queue_alerts.py -v` — FAIL.

- [ ] **Step 3: Add to `queue.py`**

```python
# append to src/bellwether/queue.py (add: from bellwether.models.extraction import Extraction)
def claim_pending_extraction(session: Session, to_status: str = "alerting") -> Extraction | None:
    extraction = session.execute(
        select(Extraction).where(Extraction.alert_status == "pending")
        .order_by(Extraction.id).with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if extraction is None:
        return None
    extraction.alert_status = to_status
    extraction.alert_claimed_at = datetime.now(timezone.utc)
    session.commit()
    return extraction


def reclaim_stale_alerting(session: Session, in_status: str, to_status: str,
                           older_than_seconds: float) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Extraction).where(Extraction.alert_status == in_status, Extraction.alert_claimed_at < cutoff)
        .values(alert_status=to_status, alert_claimed_at=None)
    )
    session.commit()
    return result.rowcount
```
(Confirm `select`/`update`/`datetime`/`timedelta`/`timezone`/`Session` are imported at the top; add the `Extraction` import.)

- [ ] **Step 4: Run test → pass; commit**

Run: `.venv/bin/python -m pytest tests/test_queue_alerts.py -v` — PASS (2 tests).
```bash
git add src/bellwether/queue.py tests/test_queue_alerts.py
git commit -m "feat: alert extraction queue (claim_pending_extraction/reclaim_stale_alerting)"
```

---

### Task 7: Alert worker stage + CLI

**Files:**
- Modify: `src/bellwether/worker.py`
- Test: `tests/test_worker_alerts.py`

**Interfaces:**
- Consumes: `Stage`; `claim_pending_extraction`/`reclaim_stale_alerting` (Task 6); `evaluate_extraction` (Task 5); `build_notifier` (Task 4).
- Produces: `make_alert_stage(notifier) -> Stage` (`process` calls `evaluate_extraction` then sets `extraction.alert_status="done"`, `alert_claimed_at=None`, `session.commit()`); `_build_stage("alert")` wires `build_notifier()` (function-local import); CLI `choices` gains `"alert"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_alerts.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.alerts.contracts import NotifyOutcome
from bellwether.worker import make_alert_stage


class StubNotifier:
    def notify(self, webhook_url, payload): return NotifyOutcome(ok=True)


def _pending_ext(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline", alert_status="pending")
    db_session.add(ex); db_session.flush()
    return ex


def test_alert_stage_processes_pending_extraction(db_session):
    ex = _pending_ext(db_session)
    db_session.add(AlertRule(owner_id=None, name="all", enabled=True, webhook_url=None, condition={}))
    db_session.flush()
    stage = make_alert_stage(StubNotifier())
    claimed = stage.claim_next(db_session)
    stage.process(db_session, claimed)
    db_session.refresh(ex)
    assert ex.alert_status == "done"
    assert db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().first() is not None
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/test_worker_alerts.py -v` — FAIL.

- [ ] **Step 3: Add `make_alert_stage` + wiring**

In `src/bellwether/worker.py`, add imports (`claim_pending_extraction`, `reclaim_stale_alerting` from `bellwether.queue`; `evaluate_extraction` from `bellwether.alerts.engine`) and:
```python
def make_alert_stage(notifier) -> Stage:
    def process(session, extraction) -> None:
        evaluate_extraction(session, extraction, notifier)
        extraction.alert_status = "done"
        extraction.alert_claimed_at = None
        session.commit()

    return Stage(
        name="alert",
        claim_next=lambda s: claim_pending_extraction(s, "alerting"),
        reclaim=lambda s, secs: reclaim_stale_alerting(s, "alerting", "pending", secs),
        process=process,
    )
```
In `_build_stage`, add before the final `return`:
```python
    if name == "alert":
        from bellwether.alerts.notifier import build_notifier
        return make_alert_stage(build_notifier())
```
Update the CLI: `choices=["detect", "extract", "resolve", "measure", "discovery", "alert"]`.

- [ ] **Step 4: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/test_worker_alerts.py tests/test_worker.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/worker.py tests/test_worker_alerts.py
git commit -m "feat: alert worker stage + CLI (python -m bellwether.worker alert)"
```

---

### Task 8: Alert-rule CRUD API + CORS

**Files:**
- Create: `src/bellwether/api/alert_rules.py`
- Modify: `src/bellwether/api/schemas.py`, `src/bellwether/api/app.py`
- Test: `tests/api/test_alert_rules_api.py`

**Interfaces:**
- Produces (schemas): `AlertCondition{min_confidence: float|None, min_magnitude: str|None, directions: list[str]|None, figure_ids: list[int]|None}` (extra keys forbidden); `AlertRuleCreate{name, condition: AlertCondition, webhook_url: str|None, enabled: bool = True}`; `AlertRuleRead` (from ORM: id, name, condition, webhook_url, enabled).
- Produces (router, authenticated, owner-scoped): `POST /alert_rules`; `GET /alert_rules`; `PATCH /alert_rules/{id}`; `DELETE /alert_rules/{id}`. Plus `CORSMiddleware` wired in `create_app()` from `settings.cors_origins`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_alert_rules_api.py
from bellwether.repositories.users import get_user_by_username


def test_requires_auth(client):
    assert client.get("/alert_rules").status_code == 401


def test_crud_owner_scoped(client, auth_headers, db_session):
    body = {"name": "strong up", "condition": {"min_confidence": 0.7, "directions": ["up"]},
            "webhook_url": "http://hook", "enabled": True}
    r = client.post("/alert_rules", json=body, headers=auth_headers)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert r.json()["condition"]["min_confidence"] == 0.7
    listed = client.get("/alert_rules", headers=auth_headers).json()
    assert any(x["id"] == rid for x in listed)
    p = client.patch(f"/alert_rules/{rid}", json={"enabled": False}, headers=auth_headers)
    assert p.status_code == 200 and p.json()["enabled"] is False
    d = client.delete(f"/alert_rules/{rid}", headers=auth_headers)
    assert d.status_code == 204
    assert client.patch("/alert_rules/999999", json={"enabled": False}, headers=auth_headers).status_code == 404


def test_condition_rejects_unknown_keys(client, auth_headers):
    bad = {"name": "x", "condition": {"bogus_field": 1}, "webhook_url": None, "enabled": True}
    assert client.post("/alert_rules", json=bad, headers=auth_headers).status_code == 422
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/api/test_alert_rules_api.py -v` — FAIL.

- [ ] **Step 3: Add schemas**

Append to `src/bellwether/api/schemas.py` (ensure `ConfigDict` imported):
```python
class AlertCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_confidence: float | None = None
    min_magnitude: str | None = None
    directions: list[str] | None = None
    figure_ids: list[int] | None = None


class AlertRuleCreate(BaseModel):
    name: str
    condition: AlertCondition = AlertCondition()
    webhook_url: str | None = None
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    condition: AlertCondition | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


class AlertRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    condition: dict
    webhook_url: str | None
    enabled: bool
```

- [ ] **Step 4: Write the router**

```python
# src/bellwether/api/alert_rules.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.alert_rule import AlertRule
from bellwether.api.schemas import AlertRuleCreate, AlertRuleUpdate, AlertRuleRead

router = APIRouter()


@router.post("/alert_rules", response_model=AlertRuleRead, status_code=status.HTTP_201_CREATED)
def create_rule(body: AlertRuleCreate, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = AlertRule(owner_id=user.id, name=body.name,
                     condition=body.condition.model_dump(exclude_none=True),
                     webhook_url=body.webhook_url, enabled=body.enabled)
    session.add(rule)
    session.flush()
    return rule


@router.get("/alert_rules", response_model=list[AlertRuleRead])
def list_rules(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return list(session.execute(
        select(AlertRule).where(AlertRule.owner_id == user.id).order_by(AlertRule.id.desc())
    ).scalars())


def _owned(session, rule_id, user):
    return session.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.owner_id == user.id)
    ).scalar_one_or_none()


@router.patch("/alert_rules/{rule_id}", response_model=AlertRuleRead)
def update_rule(rule_id: int, body: AlertRuleUpdate, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = _owned(session, rule_id, user)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if body.name is not None:
        rule.name = body.name
    if body.condition is not None:
        rule.condition = body.condition.model_dump(exclude_none=True)
    if body.webhook_url is not None:
        rule.webhook_url = body.webhook_url
    if body.enabled is not None:
        rule.enabled = body.enabled
    session.flush()
    return rule


@router.delete("/alert_rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = _owned(session, rule_id, user)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.flush()
```

- [ ] **Step 5: Wire router + CORS**

In `src/bellwether/api/app.py`: add `from fastapi.middleware.cors import CORSMiddleware`, `from bellwether.api.alert_rules import router as alert_rules_router`, `from bellwether.config import get_settings`. In `create_app`, after `app = FastAPI(...)`:
```python
    origins = get_settings().cors_origins
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
```
and `app.include_router(alert_rules_router)`.

- [ ] **Step 6: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/api/test_alert_rules_api.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/api/alert_rules.py src/bellwether/api/schemas.py src/bellwether/api/app.py tests/api/test_alert_rules_api.py
git commit -m "feat: alert-rule CRUD API + CORS"
```

---

### Task 9: SSE live feed — `/stream`

**Files:**
- Create: `src/bellwether/api/stream.py`
- Modify: `src/bellwether/api/app.py`
- Test: `tests/api/test_stream.py`

**Interfaces:**
- Consumes: `Alert` model; the JWT decode used by `get_current_user` (confirm the helper in `src/bellwether/security/` — reuse it to validate the `?token=`).
- Produces: `fetch_new_alerts(session, owner_id, after_id, limit=50) -> list[Alert]` (owner's alerts with `id > after_id`, ascending, capped); `GET /stream?token=…` → `StreamingResponse` (`text/event-stream`) emitting each new alert as `event: alert\ndata: <payload json>\n\n`, heartbeat `: ping` every ~15s; 401 without/with an invalid token.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_stream.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert import Alert
from bellwether.api.stream import fetch_new_alerts
from bellwether.repositories.users import get_user_by_username


def _alert(db_session, owner_id):
    f = Figure(name="F", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline"); db_session.add(ex); db_session.flush()
    a = Alert(extraction_id=ex.id, rule_id=None, owner_id=owner_id, payload={"text": "hi"}, webhook_status="skipped")
    db_session.add(a); db_session.flush()
    return a


def test_fetch_new_alerts_owner_scoped(db_session):
    uid = get_user_by_username(db_session, "tester").id if get_user_by_username(db_session, "tester") else 1
    a1 = _alert(db_session, uid)
    other = _alert(db_session, 999999)  # different owner
    rows = fetch_new_alerts(db_session, uid, after_id=0)
    ids = [r.id for r in rows]
    assert a1.id in ids and other.id not in ids
    assert fetch_new_alerts(db_session, uid, after_id=a1.id) == []   # nothing newer


def test_stream_requires_token(client):
    assert client.get("/stream").status_code in (401, 422)   # missing token
    assert client.get("/stream?token=bogus").status_code == 401
```

- [ ] **Step 2: Run test → fail**

Run: `.venv/bin/python -m pytest tests/api/test_stream.py -v` — FAIL.

- [ ] **Step 3: Write `stream.py`**

First inspect `src/bellwether/security/deps.py` to find how the JWT is decoded (the function `get_current_user` uses) and reuse it. Then:
```python
# src/bellwether/api/stream.py
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session, SessionLocal
from bellwether.config import get_settings
from bellwether.models.alert import Alert
from bellwether.models.user import User
# Reuse the SAME token decode as get_current_user (confirm the exact import from security/deps.py):
from bellwether.security.deps import _decode_user  # ADAPT: use whatever function get_current_user calls to resolve a token -> User

router = APIRouter()


def fetch_new_alerts(session: Session, owner_id, after_id: int, limit: int = 50) -> list[Alert]:
    return list(session.execute(
        select(Alert).where(Alert.owner_id == owner_id, Alert.id > after_id)
        .order_by(Alert.id).limit(limit)
    ).scalars())


def _user_from_token(token: str, session: Session) -> User:
    user = _decode_user(token, session)  # ADAPT to the real helper; must raise/return None on invalid
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


@router.get("/stream")
def stream(token: str = Query(...), session: Session = Depends(get_session)):
    user = _user_from_token(token, session)
    owner_id = user.id
    poll = get_settings().sse_poll_interval_seconds

    async def gen():
        with SessionLocal() as s:
            last_id = s.execute(
                select(Alert.id).where(Alert.owner_id == owner_id).order_by(Alert.id.desc()).limit(1)
            ).scalar() or 0
        ticks = 0
        while True:
            with SessionLocal() as s:
                new = fetch_new_alerts(s, owner_id, last_id)
            for a in new:
                last_id = a.id
                yield f"event: alert\ndata: {json.dumps(a.payload)}\n\n"
            ticks += 1
            if ticks % 8 == 0:
                yield ": ping\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(gen(), media_type="text/event-stream")
```
NOTE: the `_decode_user` import is a placeholder — open `security/deps.py`, find how `get_current_user` turns a bearer token into a `User` (it likely decodes the JWT with `jwt.decode` + looks up the user), and factor that into a small reusable function (or call the existing one) so `?token=` is validated identically. If `get_current_user` isn't easily reusable for a query-param token, add a tiny `resolve_token(token, session) -> User | None` in `security/deps.py` and use it in BOTH places.

- [ ] **Step 4: Wire the router**

In `src/bellwether/api/app.py`, add `from bellwether.api.stream import router as stream_router` and `app.include_router(stream_router)`.

- [ ] **Step 5: Run test → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/api/test_stream.py -q` then `.venv/bin/python -m pytest -q` — PASS. (The streaming generator itself is exercised live pre-merge; the suite tests `fetch_new_alerts` + auth.)
```bash
git add src/bellwether/api/stream.py src/bellwether/api/app.py src/bellwether/security tests/api/test_stream.py
git commit -m "feat: SSE /stream live feed (owner-scoped, token query auth, DB-polled)"
```

---

### Task 10: Track-B leaderboard + `/signals` + `/impacts`

**Files:**
- Modify: `src/bellwether/trackb/report.py`
- Create: `src/bellwether/api/feed.py`
- Modify: `src/bellwether/api/schemas.py`, `src/bellwether/api/app.py`
- Test: `tests/trackb/test_leaderboard.py`, `tests/api/test_feed_api.py`

**Interfaces:**
- Produces: `trackb/report.py::leaderboard_by_figure(session, owner_id) -> list[dict]` — per figure `{figure_id, figure_name, n, avg_pct_move, avg_abs_pct_move, directional_hit_rate}` over `measured` impacts (join `Impact→Resolution→Extraction→Statement→Figure`, `Figure.owner_id == owner_id`), sorted by `avg_abs_pct_move` desc. `directional_hit_rate` = fraction where `sign(pct_move)` matches `Extraction.direction` (`up`→>0, `down`→<0; `neutral` never a hit).
- Produces (schemas): `LeaderboardRow`, `SignalRead`, `ImpactRead`.
- Produces (routers, authenticated, owner-scoped): `GET /leaderboard`; `GET /signals?figure_id=&direction=&min_confidence=&limit=`; `GET /impacts?figure_id=&symbol=&window=&limit=`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/trackb/test_leaderboard.py
from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.trackb.report import leaderboard_by_figure


def _chain(db_session, owner_id, direction, pct_move):
    f = Figure(name="F", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="resolved"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction=direction, magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline"); db_session.add(ex); db_session.flush()
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    db_session.add(Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="1d",
                          event_at=st.published_at, due_at=st.published_at, status="measured", pct_move=pct_move))
    db_session.flush()
    return f


def test_leaderboard_hit_rate(db_session):
    f = _chain(db_session, 1, "up", 0.5)      # predicted up, moved up -> hit
    _chain_same = Statement  # noqa
    # second impact for the same figure: predicted up, moved down -> miss
    _chain(db_session, 1, "up", -0.3)
    rows = {r["figure_id"]: r for r in leaderboard_by_figure(db_session, owner_id=1)}
    # two different figures created (helper makes a new figure each call) — assert both present with hit-rates
    assert all(r["n"] == 1 for r in rows.values())
    hit = [r for r in rows.values() if r["directional_hit_rate"] == 1.0]
    miss = [r for r in rows.values() if r["directional_hit_rate"] == 0.0]
    assert len(hit) == 1 and len(miss) == 1
```

```python
# tests/api/test_feed_api.py
def test_feed_requires_auth(client):
    assert client.get("/signals").status_code == 401
    assert client.get("/impacts").status_code == 401
    assert client.get("/leaderboard").status_code == 401
```

- [ ] **Step 2: Run tests → fail**

Run: `.venv/bin/python -m pytest tests/trackb/test_leaderboard.py tests/api/test_feed_api.py -v` — FAIL.

- [ ] **Step 3: Add `leaderboard_by_figure`**

Append to `src/bellwether/trackb/report.py`:
```python
from bellwether.models.figure import Figure


def leaderboard_by_figure(session: Session, owner_id) -> list[dict]:
    rows = session.execute(
        select(Figure.id, Figure.name, Extraction.direction, Impact.pct_move)
        .join(Statement, Statement.figure_id == Figure.id)
        .join(Extraction, Extraction.statement_id == Statement.id)
        .join(Resolution, Resolution.extraction_id == Extraction.id)
        .join(Impact, Impact.resolution_id == Resolution.id)
        .where(Figure.owner_id == owner_id, Impact.status == "measured", Impact.pct_move.isnot(None))
    ).all()
    agg: dict[int, dict] = {}
    for fid, fname, direction, pct in rows:
        a = agg.setdefault(fid, {"figure_id": fid, "figure_name": fname, "moves": [], "hits": 0})
        a["moves"].append(pct)
        if (direction == "up" and pct > 0) or (direction == "down" and pct < 0):
            a["hits"] += 1
    out = []
    for a in agg.values():
        n = len(a["moves"])
        out.append({
            "figure_id": a["figure_id"], "figure_name": a["figure_name"], "n": n,
            "avg_pct_move": sum(a["moves"]) / n,
            "avg_abs_pct_move": sum(abs(m) for m in a["moves"]) / n,
            "directional_hit_rate": a["hits"] / n,
        })
    return sorted(out, key=lambda r: r["avg_abs_pct_move"], reverse=True)
```

- [ ] **Step 4: Add schemas + the feed router**

Append to `src/bellwether/api/schemas.py`:
```python
class LeaderboardRow(BaseModel):
    figure_id: int
    figure_name: str
    n: int
    avg_pct_move: float
    avg_abs_pct_move: float
    directional_hit_rate: float


class SignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    statement_id: int
    direction: str
    magnitude: str
    confidence: float
    entities: list
    version: str


class ImpactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    symbol: str
    window: str
    status: str
    pct_move: float | None
    volume_spike: float | None
```

```python
# src/bellwether/api/feed.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.trackb.report import leaderboard_by_figure
from bellwether.api.schemas import LeaderboardRow, SignalRead, ImpactRead

router = APIRouter()


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return leaderboard_by_figure(session, user.id)


@router.get("/signals", response_model=list[SignalRead])
def signals(figure_id: int | None = None, direction: str | None = None,
            min_confidence: float | None = None, limit: int = Query(default=50, ge=1, le=500),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
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


@router.get("/impacts", response_model=list[ImpactRead])
def impacts(figure_id: int | None = None, symbol: str | None = None, window: str | None = None,
            limit: int = Query(default=50, ge=1, le=500),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    q = (select(Impact).join(Resolution, Resolution.id == Impact.resolution_id)
         .join(Extraction, Extraction.id == Resolution.extraction_id)
         .join(Statement, Statement.id == Extraction.statement_id)
         .join(Figure, Figure.id == Statement.figure_id).where(Figure.owner_id == user.id))
    if figure_id is not None:
        q = q.where(Statement.figure_id == figure_id)
    if symbol is not None:
        q = q.where(Impact.symbol == symbol)
    if window is not None:
        q = q.where(Impact.window == window)
    q = q.order_by(Impact.id.desc()).limit(limit)
    return list(session.execute(q).scalars())
```

- [ ] **Step 5: Wire the router**

In `src/bellwether/api/app.py`, add `from bellwether.api.feed import router as feed_router` and `app.include_router(feed_router)`.

- [ ] **Step 6: Run tests → pass; full suite; commit**

Run: `.venv/bin/python -m pytest tests/trackb/test_leaderboard.py tests/api/test_feed_api.py -q` then `.venv/bin/python -m pytest -q` — PASS.
```bash
git add src/bellwether/trackb/report.py src/bellwether/api/feed.py src/bellwether/api/schemas.py src/bellwether/api/app.py tests/trackb/test_leaderboard.py tests/api/test_feed_api.py
git commit -m "feat: Track-B leaderboard + /signals + /impacts query APIs"
```

---

## Self-Review

**Spec coverage (7a spec):**
- alert_rules + alerts + extraction alert_status (§4) — Task 1 ✓ (migration backfills existing to `done`).
- Config (§10) — Task 2 ✓.
- Condition engine (§5) — Task 3 ✓.
- Notifier seam (§6) — Task 4 ✓ (Slack/Discord dual-key, certifi SSL, never raises).
- Alert engine (§6) — Task 5 ✓ (match → alerts → dispatch; idempotent via unique + exists-check; failed webhook recorded).
- Alert queue (§3) — Task 6 ✓ (claim commits, like the Plan-6 fix).
- Alert worker stage + CLI; worker.py stays clean (§2, §11) — Task 7 ✓ (function-local build_notifier).
- Alert-rule CRUD + CORS (§9) — Task 8 ✓ (condition `extra="forbid"`; owner-scoped).
- SSE /stream (§7) — Task 9 ✓ (DB-poll, `?token=` auth, owner-scoped).
- Leaderboard + /signals + /impacts (§8) — Task 10 ✓ (Track-B; firewall intact).
- **Extraction defaults to alert_status="pending"** so new extractions are picked up with no change to the extract stage — the column default (Task 1) handles it; noted, no separate task.
- **Deferred (§13):** impact-time alerts, webhook retry, other channels, resolved-symbol conditions, the Next.js frontend (7b) — no task, correct.

**Deliberate flags for the reviewer:**
- **Task 9 token auth** is the one integration unknown: the SSE endpoint must validate `?token=` with the SAME JWT decode `get_current_user` uses — the plan instructs the implementer to open `security/deps.py` and factor out/reuse that decode (the `_decode_user` import is a placeholder to adapt). Confirm this in review.
- Live webhook + SSE end-to-end and the rule-CRUD commit path are verified **manually pre-merge** (not in the suite) — same discipline as the market/discovery adapters and the Plan-5/6 review endpoints.

**Placeholder scan:** every code step is complete except Task 9's explicitly-flagged `_decode_user` adaptation point (an integration seam the implementer wires to the real helper). No TBD/TODO elsewhere.

**Type consistency:** `matches(condition, extraction, figure_id) -> bool` (Task 3) is consumed identically in `evaluate_extraction` (Task 5). `Notifier.notify(webhook_url, payload) -> NotifyOutcome` (Task 4) matches the engine + worker (Tasks 5, 7) + the stub notifiers in tests. `claim_pending_extraction`/`reclaim_stale_alerting` (Task 6) match the Stage lambdas (Task 7). `Alert`/`AlertRule` columns (Task 1) match every access in Tasks 5/8/9/10. `fetch_new_alerts(session, owner_id, after_id)` (Task 9) is used by the SSE generator. `leaderboard_by_figure(session, owner_id) -> list[dict]` (Task 10) matches the `/leaderboard` route + `LeaderboardRow` schema. The `alert` CLI choice (Task 7) extends the Plan-6 `choices` list.
