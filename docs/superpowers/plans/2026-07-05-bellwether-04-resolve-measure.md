# bellwether Plan 4 — Resolve & Measure Implementation Plan

> **Status: ✅ Complete** — merged to `main` (2026-07-05) via subagent-driven development. All 11 tasks implemented TDD-style, each task-reviewed plus a whole-branch review. Fixes applied: the resolve-stage cache-insert rollback scoped to a SAVEPOINT (`begin_nested`) for concurrency; the Measure volume-baseline lookback scaled to `window × baseline_bars`; the `MarketDataError` boundary widened; F401 cleanup. A **live end-to-end run** then caught one more real bug the stub tests couldn't — the Measure fetch `end` was too tight for daily bars, so the `1d` window never measured; fixed by padding `end` past `due_at`, verified live (TSLA 2024-05-01 → +0.67%). Ships **yfinance-only** (CoinGecko deferred behind the registry seam, owner-approved). Suite: 87/87, pristine, `worker.py` ruff-clean. Checkboxes left as the original plan of record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Plan 3's `extractions` into measured market impact via two more pipeline stages — **Resolve** (entity→symbol, LLM-proposed + yfinance-verified + self-cached) and **Measure** (delayed per-window price impact) — reusing and generalizing Plan 3's worker/queue harness.

**Architecture:** Builds on Plan 3 (queue harness, DSPy stages, worker CLI). Adds three shared-corpus tables (`resolutions`, `entity_symbols` cache, `impacts`); a market-data adapter (`MarketData` interface + yfinance adapter, behind a registry) with a pure `compute_impact` math function; a DSPy `Resolve` module behind a frozen `Resolver` contract that runs a **bounded propose→verify→refine loop** (yfinance is the deterministic arbiter, LLM only proposes); a Resolve stage that caches verified symbols in `entity_symbols` and pre-creates `pending` `impacts` rows; and a Measure stage that claims *due* `impacts` (`SKIP LOCKED … AND due_at ≤ now()`) and fills in the realized move. The worker `Stage` is generalized to carry its own `claim_next`/`reclaim` so one loop serves both a status-claim stage (Resolve, on `statements`) and a due-claim stage (Measure, on `impacts`); Detect/Extract are rewrapped, behavior unchanged.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 (sync), Alembic, Postgres (`FOR UPDATE SKIP LOCKED`), DSPy (via LiteLLM), **yfinance** (+ pandas), pytest. Design spec: `docs/superpowers/specs/2026-07-05-bellwether-04-resolve-measure-design.md`.

## Global Constraints

- Python **3.11+**. Postgres via `postgresql+psycopg://` (psycopg 3). SQLAlchemy **2.0** sync (`Mapped`/`mapped_column`); JSONB via `sqlalchemy.dialects.postgresql`.
- **Shared corpus:** `resolutions`, `entity_symbols`, `impacts` carry **NO `owner_id`** (symmetric with `statements`/`detections`/`extractions`).
- **No fabrication into the oracle:** a symbol is `measurable=True` only when the deterministic verifier (yfinance) confirms it exists AND its real instrument name matches the LLM's proposed name AND confidence ≥ threshold. The LLM proposes; the verifier decides.
- **DSPy-only, provider-agnostic:** Resolve's LLM is a DSPy module via LiteLLM; model is config-selected (`resolve_model`); credential from env (no provider key in `Settings`).
- **Firewall:** `resolutions`/`impacts` are Track-B; never read by a Detect/Extract input path. (The metric-path assertion is Plan 5.)
- **Terminal vs. retryable:** insufficient market data → terminal `measure_failed`; a transient adapter error → propagates for reclaim-retry (never burned to `measure_failed`). Resolve give-up after the bounded loop is a normal non-measurable result; a transient LLM/verifier error propagates. The market adapter exposes transient failures as a paradigm-agnostic `MarketDataError` so the stage/worker layer never imports yfinance internals (mirrors Plan 3's `ExtractionParseError`).
- **Status vocabulary:** statements add `resolving` (transient) → `resolved` (terminal). impacts: `pending` → `measuring` (transient) → `measured` | `measure_failed`.
- **Tests use REAL Postgres, NO live network:** LLM via `dspy.utils.DummyLM`; the market adapter/verifier via injected stubs in stage/e2e tests; the adapter's own parsing via synthetic pandas frames; `compute_impact` is pure (synthetic series). No yfinance/network call in any test.
- **CoinGecko is deferred** (see Self-Review): v1 uses the yfinance adapter for all asset classes (it serves crypto via `X-USD` pairs); the registry keeps `asset_class` routing so CoinGecko can slot in later behind the same `MarketData` interface.
- **ENVIRONMENT:** run everything via `.venv/bin/python -m …` / `.venv/bin/alembic …` (the shell's `python`/`pytest`/`alembic` are shadowed). Postgres via `docker compose up -d`; `.env` exists.

## File Structure

```
src/bellwether/
├── models/
│   ├── __init__.py            # (modify) register Resolution, EntitySymbol, Impact
│   ├── resolution.py          # Resolution
│   ├── entity_symbol.py       # EntitySymbol (self-building cache)
│   └── impact.py              # Impact (result + due-queue)
├── config.py                  # (modify) resolve/measure settings
├── windows.py                 # parse_window / parse_windows
├── market/
│   ├── __init__.py
│   ├── base.py                # InstrumentInfo, SymbolCandidate, PriceBar, PriceSeries, MarketDataError, SymbolVerifier, MarketData
│   ├── yfinance_adapter.py    # YFinanceAdapter (+ pure _series_from_history)
│   └── registry.py            # build_market_data()
├── measure/
│   ├── __init__.py
│   └── impact.py              # ImpactPoint + compute_impact() (pure)
├── llm/
│   ├── contracts.py           # (modify) ResolveContext, ResolutionOutcome, Resolver
│   └── resolve.py             # ResolveSig, Resolve module, bounded-loop adapter, build_resolver, normalize_entity
├── queue.py                   # (modify) claim_due_impact + reclaim_stale_impacts
└── worker.py                  # (modify) generalize Stage; add make_resolve_stage, make_measure_stage; CLI resolve/measure
migrations/versions/           # ONE migration: resolutions, entity_symbols, impacts
tests/
├── test_models_resolve_measure.py
├── test_config_resolve_measure.py
├── test_windows.py
├── market/__init__.py, market/test_base.py, market/test_yfinance_adapter.py
├── measure/__init__.py, measure/test_impact.py
├── llm/test_resolve.py
├── test_queue_impacts.py
└── test_worker_resolve_measure.py
```

---

### Task 1: Models + migration (resolutions, entity_symbols, impacts)

**Files:**
- Create: `src/bellwether/models/resolution.py`, `src/bellwether/models/entity_symbol.py`, `src/bellwether/models/impact.py`
- Modify: `src/bellwether/models/__init__.py`
- Create: the generated migration under `migrations/versions/`
- Test: `tests/test_models_resolve_measure.py`

**Interfaces:**
- Consumes: `Base`, `Extraction` (Plan 3).
- Produces:
  - `Resolution` (`resolutions`): `id` PK, `extraction_id` FK→extractions.id (CASCADE, indexed), `entity` str, `symbol` str|null, `asset_class` str|null, `measurable` bool, `created_at`. No `owner_id`.
  - `EntitySymbol` (`entity_symbols`): `id` PK, `normalized_entity` str **unique**, `symbol` str|null, `asset_class` str|null, `measurable` bool, `instrument_name` str|null, `confidence` float|null, `source` str default `"llm"`, `created_at`. No `owner_id`.
  - `Impact` (`impacts`): `id` PK, `resolution_id` FK→resolutions.id (CASCADE, indexed), `symbol` str, `asset_class` str, `window` str, `event_at` tz datetime, `due_at` tz datetime (indexed), `status` str default `"pending"` (indexed), `price_t0`/`price_after`/`pct_move`/`volume_spike` float|null, `measured_at` tz datetime|null, `claimed_at` tz datetime|null, `created_at`. **Unique `(resolution_id, window)`.** No `owner_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_resolve_measure.py
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact


def test_resolution_columns():
    cols = set(Resolution.__table__.columns.keys())
    assert {"id", "extraction_id", "entity", "symbol", "asset_class", "measurable", "created_at"} <= cols
    assert "owner_id" not in cols


def test_entity_symbol_columns_and_unique():
    cols = set(EntitySymbol.__table__.columns.keys())
    assert {"id", "normalized_entity", "symbol", "asset_class", "measurable",
            "instrument_name", "confidence", "source", "created_at"} <= cols
    assert "owner_id" not in cols
    assert EntitySymbol.__table__.columns["normalized_entity"].unique is True


def test_impact_columns_and_unique():
    cols = set(Impact.__table__.columns.keys())
    assert {"id", "resolution_id", "symbol", "asset_class", "window", "event_at", "due_at",
            "status", "price_t0", "price_after", "pct_move", "volume_spike",
            "measured_at", "claimed_at", "created_at"} <= cols
    assert "owner_id" not in cols
    uniques = [c for c in Impact.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"resolution_id", "window"} for u in uniques)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models_resolve_measure.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.models.resolution`.

- [ ] **Step 3: Write the Resolution model**

```python
# src/bellwether/models/resolution.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Resolution(Base):
    __tablename__ = "resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity: Mapped[str] = mapped_column(String(300), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    measurable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Write the EntitySymbol model**

```python
# src/bellwether/models/entity_symbol.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class EntitySymbol(Base):
    __tablename__ = "entity_symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_entity: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    measurable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    instrument_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: Write the Impact model**

```python
# src/bellwether/models/impact.py
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Impact(Base):
    __tablename__ = "impacts"
    __table_args__ = (
        UniqueConstraint("resolution_id", "window", name="uq_impacts_resolution_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resolution_id: Mapped[int] = mapped_column(
        ForeignKey("resolutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    window: Mapped[str] = mapped_column(String(10), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    price_t0: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_spike: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 6: Register the models for Alembic**

```python
# src/bellwether/models/__init__.py
from bellwether.models.base import Base
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact

__all__ = ["Base", "User", "Figure", "Source", "Statement", "Detection", "Extraction",
           "Resolution", "EntitySymbol", "Impact"]
```

- [ ] **Step 7: Run the model test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models_resolve_measure.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Generate and apply the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "create resolutions, entity_symbols, impacts"
.venv/bin/alembic upgrade head
```
Expected: a new version file; `upgrade head` completes. Open it and confirm it creates all three tables with the CASCADE FKs, the indexes (`resolutions.extraction_id`, `impacts.resolution_id`, `impacts.due_at`, `impacts.status`), the `entity_symbols.normalized_entity` unique, and the `uq_impacts_resolution_window` unique. Do not hand-edit beyond autogenerate output.

- [ ] **Step 9: Commit**

```bash
git add src/bellwether/models tests/test_models_resolve_measure.py migrations
git commit -m "feat: resolutions/entity_symbols/impacts models + migration"
```

---

### Task 2: Config — resolve & measure settings

**Files:**
- Modify: `src/bellwether/config.py`, `.env.example`
- Test: `tests/test_config_resolve_measure.py`

**Interfaces:**
- Produces on `Settings` (defaults): `resolve_model: str = "anthropic/claude-haiku-4-5"`, `resolve_max_attempts: int = 3`, `resolve_confidence_threshold: float = 0.5`, `measure_windows: str = "5m,1h,1d"`, `measure_baseline_bars: int = 20`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_resolve_measure.py
from bellwether.config import Settings


def test_resolve_measure_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.resolve_model == "anthropic/claude-haiku-4-5"
    assert s.resolve_max_attempts == 3
    assert s.resolve_confidence_threshold == 0.5
    assert s.measure_windows == "5m,1h,1d"
    assert s.measure_baseline_bars == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_resolve_measure.py -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the settings**

In `src/bellwether/config.py`, add inside `class Settings` after the Plan-3 worker settings:
```python
    resolve_model: str = "anthropic/claude-haiku-4-5"
    resolve_max_attempts: int = 3
    resolve_confidence_threshold: float = 0.5
    measure_windows: str = "5m,1h,1d"
    measure_baseline_bars: int = 20
```

- [ ] **Step 4: Document in `.env.example`**

Append to `.env.example`:
```bash
# --- Resolve & Measure (Plan 4) ---
# RESOLVE_MODEL=anthropic/claude-haiku-4-5     # LLM that proposes entity->symbol (yfinance verifies)
# RESOLVE_MAX_ATTEMPTS=3                        # bounded propose->verify->refine attempts
# RESOLVE_CONFIDENCE_THRESHOLD=0.5
# MEASURE_WINDOWS=5m,1h,1d                      # impact windows after published_at
# MEASURE_BASELINE_BARS=20                      # bars before the event used for the volume baseline
# (yfinance/CoinGecko are keyless — no market-data credential needed.)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_resolve_measure.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/config.py .env.example tests/test_config_resolve_measure.py
git commit -m "feat: resolve/measure settings"
```

---

### Task 3: Window parsing

**Files:**
- Create: `src/bellwether/windows.py`
- Test: `tests/test_windows.py`

**Interfaces:**
- Produces:
  - `parse_window(window: str) -> datetime.timedelta` — `"5m"`→5 min, `"1h"`→1 hour, `"1d"`→1 day. Raises `ValueError` on an unknown unit/format.
  - `parse_windows(spec: str) -> list[tuple[str, timedelta]]` — splits a comma spec, returns `[(name, delta), …]` preserving order, skipping empty segments.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_windows.py
from datetime import timedelta
import pytest
from bellwether.windows import parse_window, parse_windows


def test_parse_window_units():
    assert parse_window("5m") == timedelta(minutes=5)
    assert parse_window("1h") == timedelta(hours=1)
    assert parse_window("1d") == timedelta(days=1)


def test_parse_window_rejects_bad():
    with pytest.raises(ValueError):
        parse_window("5x")
    with pytest.raises(ValueError):
        parse_window("h")


def test_parse_windows_list():
    assert parse_windows("5m,1h,1d") == [
        ("5m", timedelta(minutes=5)), ("1h", timedelta(hours=1)), ("1d", timedelta(days=1))
    ]
    assert parse_windows("5m, ,1d") == [("5m", timedelta(minutes=5)), ("1d", timedelta(days=1))]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_windows.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.windows`.

- [ ] **Step 3: Write the parser**

```python
# src/bellwether/windows.py
from datetime import timedelta

_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_window(window: str) -> timedelta:
    """Parse a window like '5m' / '1h' / '1d' into a timedelta."""
    w = window.strip()
    if len(w) < 2 or not w[:-1].isdigit() or w[-1] not in _UNITS:
        raise ValueError(f"invalid window: {window!r}")
    return timedelta(**{_UNITS[w[-1]]: int(w[:-1])})


def parse_windows(spec: str) -> list[tuple[str, timedelta]]:
    """Parse a comma spec like '5m,1h,1d' into [(name, timedelta), ...]."""
    out: list[tuple[str, timedelta]] = []
    for part in spec.split(","):
        name = part.strip()
        if not name:
            continue
        out.append((name, parse_window(name)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_windows.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/windows.py tests/test_windows.py
git commit -m "feat: window spec parsing (5m/1h/1d)"
```

---

### Task 4: Market-data contracts

**Files:**
- Create: `src/bellwether/market/__init__.py` (empty), `src/bellwether/market/base.py`
- Test: `tests/market/__init__.py` (empty), `tests/market/test_base.py`

**Interfaces:**
- Produces:
  - `InstrumentInfo(symbol: str, name: str, asset_class: str)` — frozen dataclass.
  - `SymbolCandidate(symbol: str, name: str)` — frozen dataclass.
  - `PriceBar(ts: datetime, price: float, volume: float)` — frozen dataclass.
  - `PriceSeries(bars: list[PriceBar])` — frozen dataclass (bars ascending by ts).
  - `MarketDataError(Exception)` — transient adapter failure; paradigm-agnostic.
  - `SymbolVerifier` — `runtime_checkable` Protocol: `lookup(symbol, asset_class) -> InstrumentInfo | None`, `search(query) -> list[SymbolCandidate]`.
  - `MarketData` — `runtime_checkable` Protocol extending `SymbolVerifier` with `price_series(symbol, asset_class, start, end, window) -> PriceSeries`.

- [ ] **Step 1: Write the failing test**

```python
# tests/market/test_base.py
from datetime import datetime, timezone
from bellwether.market.base import (
    InstrumentInfo, SymbolCandidate, PriceBar, PriceSeries,
    MarketDataError, SymbolVerifier, MarketData,
)


def test_dataclasses_hold_fields():
    info = InstrumentInfo(symbol="TSLA", name="Tesla Inc", asset_class="equity")
    assert info.symbol == "TSLA" and info.name == "Tesla Inc"
    bar = PriceBar(ts=datetime(2026, 7, 1, tzinfo=timezone.utc), price=1.0, volume=2.0)
    assert PriceSeries(bars=[bar]).bars[0].price == 1.0
    assert SymbolCandidate(symbol="TSLA", name="Tesla").symbol == "TSLA"


def test_market_data_error_is_exception():
    assert issubclass(MarketDataError, Exception)


def test_stub_satisfies_protocols():
    class Stub:
        def lookup(self, symbol, asset_class): return None
        def search(self, query): return []
        def price_series(self, symbol, asset_class, start, end, window): return PriceSeries(bars=[])
    assert isinstance(Stub(), SymbolVerifier)
    assert isinstance(Stub(), MarketData)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/market/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.market.base`.

- [ ] **Step 3: Write the contracts**

Create empty `src/bellwether/market/__init__.py` and empty `tests/market/__init__.py`, then:
```python
# src/bellwether/market/base.py
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    asset_class: str


@dataclass(frozen=True)
class SymbolCandidate:
    symbol: str
    name: str


@dataclass(frozen=True)
class PriceBar:
    ts: datetime
    price: float
    volume: float


@dataclass(frozen=True)
class PriceSeries:
    bars: list[PriceBar]


class MarketDataError(Exception):
    """Transient market-data adapter failure (network/timeout/rate-limit). Paradigm-
    agnostic so the stage/worker layer never imports a provider's exception types."""


@runtime_checkable
class SymbolVerifier(Protocol):
    def lookup(self, symbol: str, asset_class: str) -> InstrumentInfo | None: ...
    def search(self, query: str) -> list[SymbolCandidate]: ...


@runtime_checkable
class MarketData(SymbolVerifier, Protocol):
    def price_series(self, symbol: str, asset_class: str,
                     start: datetime, end: datetime, window: str) -> PriceSeries: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/market/test_base.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/market/__init__.py src/bellwether/market/base.py tests/market
git commit -m "feat: market-data contracts (MarketData/SymbolVerifier + dataclasses)"
```

---

### Task 5: Impact math (pure)

**Files:**
- Create: `src/bellwether/measure/__init__.py` (empty), `src/bellwether/measure/impact.py`
- Test: `tests/measure/__init__.py` (empty), `tests/measure/test_impact.py`

**Interfaces:**
- Consumes: `PriceSeries`, `PriceBar` (Task 4).
- Produces:
  - `ImpactPoint(price_t0, price_after, pct_move, volume_spike)` — frozen dataclass.
  - `compute_impact(series, event_at, window, baseline_bars) -> ImpactPoint | None` — `price_t0` = last bar at/≤ `event_at`; `price_after` = first bar at/≥ `event_at+window`; `pct_move = (after-t0)/t0`; `volume_spike` = window volume ÷ baseline average (average volume of the `baseline_bars` bars at/≤ `event_at`). Returns `None` on insufficient data (no bracketing bars) or `price_t0 == 0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/measure/test_impact.py
from datetime import datetime, timedelta, timezone
from bellwether.market.base import PriceSeries, PriceBar
from bellwether.measure.impact import compute_impact, ImpactPoint

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _series(points):
    return PriceSeries(bars=[PriceBar(ts=t, price=p, volume=v) for t, p, v in points])


def test_rise_and_volume_spike():
    s = _series([
        (T0 - timedelta(minutes=2), 100.0, 10.0),
        (T0 - timedelta(minutes=1), 100.0, 10.0),
        (T0,                        100.0, 10.0),
        (T0 + timedelta(minutes=5), 110.0, 50.0),
    ])
    r = compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3)
    assert isinstance(r, ImpactPoint)
    assert r.price_t0 == 100.0 and r.price_after == 110.0
    assert abs(r.pct_move - 0.10) < 1e-9
    assert abs(r.volume_spike - 5.0) < 1e-9  # 50 / avg(10,10,10)


def test_insufficient_after_bar_returns_none():
    s = _series([(T0 - timedelta(minutes=1), 100.0, 10.0), (T0, 100.0, 10.0)])
    assert compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3) is None


def test_no_prior_bar_returns_none():
    s = _series([(T0 + timedelta(minutes=5), 110.0, 50.0)])
    assert compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3) is None


def test_fall_move():
    s = _series([(T0, 100.0, 10.0), (T0 + timedelta(hours=1), 90.0, 10.0)])
    r = compute_impact(s, T0, timedelta(hours=1), baseline_bars=1)
    assert abs(r.pct_move + 0.10) < 1e-9  # -10%
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/measure/test_impact.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.measure.impact`.

- [ ] **Step 3: Write the impact math**

Create empty `src/bellwether/measure/__init__.py` and empty `tests/measure/__init__.py`, then:
```python
# src/bellwether/measure/impact.py
from dataclasses import dataclass
from datetime import datetime, timedelta
from bellwether.market.base import PriceSeries


@dataclass(frozen=True)
class ImpactPoint:
    price_t0: float
    price_after: float
    pct_move: float
    volume_spike: float


def compute_impact(series: PriceSeries, event_at: datetime, window: timedelta,
                   baseline_bars: int) -> ImpactPoint | None:
    """Realized move over `window` after `event_at`. None if the series can't bracket it."""
    bars = sorted(series.bars, key=lambda b: b.ts)
    if not bars:
        return None
    after_target = event_at + window
    prior = [b for b in bars if b.ts <= event_at]
    after = [b for b in bars if b.ts >= after_target]
    if not prior or not after:
        return None
    b0 = prior[-1]
    b1 = after[0]
    if b0.price == 0:
        return None
    pct_move = (b1.price - b0.price) / b0.price
    window_bars = [b for b in bars if event_at < b.ts <= after_target]
    window_volume = sum(b.volume for b in window_bars) if window_bars else b1.volume
    baseline = prior[-baseline_bars:]
    baseline_avg = sum(b.volume for b in baseline) / len(baseline) if baseline else 0.0
    volume_spike = window_volume / baseline_avg if baseline_avg > 0 else 0.0
    return ImpactPoint(price_t0=b0.price, price_after=b1.price,
                       pct_move=pct_move, volume_spike=volume_spike)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/measure/test_impact.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/measure/__init__.py src/bellwether/measure/impact.py tests/measure
git commit -m "feat: pure impact math (pct_move + volume_spike)"
```

---

### Task 6: yfinance adapter + registry

**Files:**
- Create: `src/bellwether/market/yfinance_adapter.py`, `src/bellwether/market/registry.py`
- Modify: `pyproject.toml` (add `yfinance`)
- Test: `tests/market/test_yfinance_adapter.py`

**Interfaces:**
- Consumes: `InstrumentInfo`, `SymbolCandidate`, `PriceSeries`, `PriceBar`, `MarketDataError` (Task 4).
- Produces:
  - `_series_from_history(df) -> PriceSeries` — **pure**: maps a yfinance history DataFrame (DatetimeIndex + `Close`/`Volume`) to a `PriceSeries`, coercing tz-naive timestamps to UTC, sorted ascending.
  - `YFinanceAdapter` — implements `MarketData`: `lookup` (via `yf.Ticker(symbol).info` longName/shortName), `search` (via `yf.Search(query).quotes`), `price_series` (via `yf.Ticker(symbol).history(start,end,interval)` with `{5m:"5m",1h:"60m",1d:"1d"}`). Every yfinance call is wrapped so any exception re-raises as `MarketDataError`.
  - `registry.build_market_data() -> MarketData` — returns a `YFinanceAdapter` (v1 covers all asset classes; CoinGecko deferred behind this factory).

- [ ] **Step 1: Add the yfinance dependency**

In `pyproject.toml`, add `"yfinance>=0.2",` to `[project].dependencies`, then install:
```bash
.venv/bin/python -m pip install -e ".[dev]"
```
Expected: yfinance (and pandas) install.

- [ ] **Step 2: Write the failing test**

```python
# tests/market/test_yfinance_adapter.py
from datetime import datetime, timezone
import pandas as pd
from bellwether.market.yfinance_adapter import _series_from_history
from bellwether.market.base import PriceSeries


def test_series_from_history_parses_and_utc():
    idx = pd.to_datetime([
        datetime(2026, 7, 1, 12, 0), datetime(2026, 7, 1, 12, 5),
    ])
    df = pd.DataFrame({"Close": [100.0, 110.0], "Volume": [10, 50]}, index=idx)
    series = _series_from_history(df)
    assert isinstance(series, PriceSeries)
    assert len(series.bars) == 2
    assert series.bars[0].price == 100.0 and series.bars[1].price == 110.0
    assert series.bars[0].volume == 10.0
    assert series.bars[0].ts.tzinfo is not None  # coerced to UTC
    assert series.bars[0].ts <= series.bars[1].ts  # ascending
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/market/test_yfinance_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.market.yfinance_adapter`.

- [ ] **Step 4: Write the adapter**

```python
# src/bellwether/market/yfinance_adapter.py
from datetime import datetime, timezone
import yfinance as yf
from bellwether.market.base import (
    InstrumentInfo, SymbolCandidate, PriceBar, PriceSeries, MarketDataError,
)

_INTERVAL = {"5m": "5m", "1h": "60m", "1d": "1d"}


def _series_from_history(df) -> PriceSeries:
    bars: list[PriceBar] = []
    for ts, row in df.iterrows():
        py_ts = ts.to_pydatetime()
        if py_ts.tzinfo is None:
            py_ts = py_ts.replace(tzinfo=timezone.utc)
        vol = row.get("Volume", 0)
        bars.append(PriceBar(ts=py_ts, price=float(row["Close"]),
                             volume=float(vol if vol == vol and vol is not None else 0.0)))
    bars.sort(key=lambda b: b.ts)
    return PriceSeries(bars=bars)


class YFinanceAdapter:
    def lookup(self, symbol: str, asset_class: str) -> InstrumentInfo | None:
        try:
            info = yf.Ticker(symbol).info
        except Exception as exc:  # network/parse — transient
            raise MarketDataError(str(exc)) from exc
        name = info.get("longName") or info.get("shortName")
        if not name:
            return None
        return InstrumentInfo(symbol=symbol, name=name, asset_class=asset_class)

    def search(self, query: str) -> list[SymbolCandidate]:
        try:
            quotes = yf.Search(query).quotes
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc
        out: list[SymbolCandidate] = []
        for q in quotes:
            sym = q.get("symbol")
            if sym:
                out.append(SymbolCandidate(symbol=sym,
                                           name=q.get("longname") or q.get("shortname") or ""))
        return out

    def price_series(self, symbol, asset_class, start, end, window) -> PriceSeries:
        interval = _INTERVAL.get(window, "1d")
        try:
            df = yf.Ticker(symbol).history(start=start, end=end, interval=interval)
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc
        return _series_from_history(df)
```

```python
# src/bellwether/market/registry.py
from bellwether.market.base import MarketData
from bellwether.market.yfinance_adapter import YFinanceAdapter


def build_market_data() -> MarketData:
    """v1: the yfinance adapter serves all asset classes (crypto via X-USD pairs).
    A CoinGecko adapter for asset_class=crypto can slot in behind this factory later."""
    return YFinanceAdapter()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/market/test_yfinance_adapter.py -v`
Expected: PASS (1 test). (The network methods are not unit-tested — only the pure `_series_from_history` parser is.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/bellwether/market/yfinance_adapter.py src/bellwether/market/registry.py tests/market/test_yfinance_adapter.py
git commit -m "feat: yfinance market-data adapter + registry"
```

---

### Task 7: Resolver contracts + bounded propose→verify→refine loop

**Files:**
- Modify: `src/bellwether/llm/contracts.py`
- Create: `src/bellwether/llm/resolve.py`
- Test: `tests/llm/test_resolve.py`

**Interfaces:**
- Consumes: `make_lm` (Plan 3), `get_settings`, `SymbolVerifier`/`InstrumentInfo`/`SymbolCandidate`/`MarketDataError` (Task 4), `build_market_data` (Task 6).
- Produces (in `contracts.py`): `ResolveContext(figure_name: str, snippet: str)`, `ResolutionOutcome(symbol: str|None, asset_class: str|None, measurable: bool, instrument_name: str|None, confidence: float|None)`, `Resolver` Protocol (`model: str`, `resolve(entity, context) -> ResolutionOutcome`).
- Produces (in `resolve.py`): `normalize_entity(entity) -> str`; `ResolveSig`; `Resolve(dspy.Module)`; `build_resolver(lm=None, verifier=None, max_attempts=None, threshold=None) -> Resolver`. The adapter runs the bounded loop: propose → verify via `verifier.lookup` (name fuzzy-match + confidence ≥ threshold) → on failure `verifier.search` and retry with feedback → after `max_attempts` give up (non-measurable). `is_tradable=false` or empty symbol → non-measurable immediately. `MarketDataError` from the verifier propagates (transient). Accept only when a verify passed (the deterministic gate).

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_resolve.py
from dspy.utils import DummyLM
from bellwether.llm.resolve import build_resolver, normalize_entity
from bellwether.llm.contracts import ResolveContext, ResolutionOutcome
from bellwether.market.base import InstrumentInfo, SymbolCandidate

CTX = ResolveContext(figure_name="Elon Musk", snippet="Tesla will grow next quarter.")


class StubVerifier:
    def __init__(self, infos): self._infos = infos          # {symbol: InstrumentInfo|None}
    def lookup(self, symbol, asset_class): return self._infos.get(symbol)
    def search(self, query): return [SymbolCandidate("TSLA", "Tesla Inc")]


def _answer(**kw):
    base = {"reasoning": "…", "is_tradable": "True", "symbol": "TSLA",
            "asset_class": "equity", "instrument_name": "Tesla", "confidence": "0.9"}
    base.update(kw)
    return base


def test_normalize_entity():
    assert normalize_entity("  Tesla ") == "tesla"


def test_accepts_verified_symbol():
    lm = DummyLM([_answer()])
    verifier = StubVerifier({"TSLA": InstrumentInfo("TSLA", "Tesla Inc", "equity")})
    out = build_resolver(lm=lm, verifier=verifier).resolve("Tesla", CTX)
    assert isinstance(out, ResolutionOutcome)
    assert out.measurable is True and out.symbol == "TSLA" and out.asset_class == "equity"


def test_not_tradable_is_non_measurable():
    lm = DummyLM([_answer(is_tradable="False", symbol="")])
    out = build_resolver(lm=lm, verifier=StubVerifier({})).resolve("monetary policy", CTX)
    assert out.measurable is False and out.symbol is None


def test_gives_up_after_max_attempts_when_unverified():
    # verifier always returns None -> every attempt fails -> non-measurable
    lm = DummyLM([_answer(), _answer()])
    out = build_resolver(lm=lm, verifier=StubVerifier({}), max_attempts=2).resolve("Tesla", CTX)
    assert out.measurable is False
```

> If `DummyLM(...)` reports a signature mismatch on the installed dspy (3.2.1 in this repo), inspect `help(dspy.utils.DummyLM)` and adjust only the answer-dict literals — the asserted behavior (accept / non-measurable / give-up) is unchanged. `ChainOfThought` needs the `reasoning` key.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.llm.resolve`.

- [ ] **Step 3: Add the resolver contracts**

Append to `src/bellwether/llm/contracts.py`:
```python
@dataclass(frozen=True)
class ResolveContext:
    figure_name: str
    snippet: str


@dataclass(frozen=True)
class ResolutionOutcome:
    symbol: str | None
    asset_class: str | None
    measurable: bool
    instrument_name: str | None
    confidence: float | None


@runtime_checkable
class Resolver(Protocol):
    model: str
    def resolve(self, entity: str, context: ResolveContext) -> ResolutionOutcome: ...
```

- [ ] **Step 4: Write the resolver**

```python
# src/bellwether/llm/resolve.py
from typing import Literal
import dspy
from dspy.utils.exceptions import AdapterParseError
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import ResolveContext, ResolutionOutcome, Resolver


def normalize_entity(entity: str) -> str:
    return entity.strip().lower()


def _names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


class ResolveSig(dspy.Signature):
    """Map a named entity to a tradable market symbol, using the figure and statement
    snippet as disambiguation context. Set is_tradable=false when there is no honest
    tradable instrument for the entity (e.g. an abstract concept)."""
    entity: str = dspy.InputField()
    figure_name: str = dspy.InputField()
    snippet: str = dspy.InputField()
    feedback: str = dspy.InputField(desc="why the previous attempt failed to verify; empty on the first try")
    is_tradable: bool = dspy.OutputField()
    symbol: str = dspy.OutputField(desc="ticker/pair, e.g. TSLA, GLD, BTC-USD; empty if not tradable")
    asset_class: Literal["equity", "etf", "index", "fx", "crypto"] = dspy.OutputField()
    instrument_name: str = dspy.OutputField(desc="the instrument's real name, e.g. Tesla Inc.")
    confidence: float = dspy.OutputField(desc="0.0-1.0")


class Resolve(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predict = dspy.ChainOfThought(ResolveSig)

    def forward(self, entity, figure_name, snippet, feedback) -> dspy.Prediction:
        return self.predict(entity=entity, figure_name=figure_name, snippet=snippet, feedback=feedback)


class _ResolverAdapter:
    def __init__(self, module: Resolve, model: str, verifier, max_attempts: int, threshold: float):
        self._module = module
        self.model = model
        self._verifier = verifier
        self._max_attempts = max_attempts
        self._threshold = threshold

    def resolve(self, entity: str, context: ResolveContext) -> ResolutionOutcome:
        feedback = ""
        for _ in range(self._max_attempts):
            try:
                pred = self._module(entity=entity, figure_name=context.figure_name,
                                    snippet=context.snippet, feedback=feedback)
            except AdapterParseError:
                feedback = "Your previous response could not be parsed. Return the fields exactly."
                continue
            if not bool(pred.is_tradable) or not str(pred.symbol).strip():
                return ResolutionOutcome(None, None, False, None, None)
            symbol = str(pred.symbol).strip()
            asset_class = str(pred.asset_class)
            confidence = float(pred.confidence)
            # Deterministic gate: yfinance verifies existence + name; MarketDataError propagates.
            info = self._verifier.lookup(symbol, asset_class)
            if info is not None and confidence >= self._threshold \
                    and _names_match(str(pred.instrument_name), info.name):
                return ResolutionOutcome(info.symbol, info.asset_class, True, info.name, confidence)
            candidates = self._verifier.search(str(pred.instrument_name) or entity)
            cand = ", ".join(f"{c.symbol} ({c.name})" for c in candidates[:5]) or "none"
            feedback = (f"Symbol {symbol!r} did not verify. Candidates: {cand}. "
                        f"Pick the correct symbol or set is_tradable=false.")
        return ResolutionOutcome(None, None, False, None, None)


def build_resolver(lm: dspy.LM | None = None, verifier=None,
                   max_attempts: int | None = None, threshold: float | None = None) -> Resolver:
    settings = get_settings()
    if verifier is None:
        from bellwether.market.registry import build_market_data
        verifier = build_market_data()
    module = Resolve()
    module.set_lm(lm or make_lm(settings.resolve_model))
    return _ResolverAdapter(
        module, settings.resolve_model, verifier,
        max_attempts if max_attempts is not None else settings.resolve_max_attempts,
        threshold if threshold is not None else settings.resolve_confidence_threshold,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_resolve.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/llm/contracts.py src/bellwether/llm/resolve.py tests/llm/test_resolve.py
git commit -m "feat: Resolver contract + bounded propose/verify/refine resolver (yfinance-verified)"
```

---

### Task 8: Queue — due-claim for impacts

**Files:**
- Modify: `src/bellwether/queue.py`
- Test: `tests/test_queue_impacts.py`

**Interfaces:**
- Consumes: `Impact` (Task 1).
- Produces:
  - `claim_due_impact(session, to_status="measuring") -> Impact | None` — `SELECT … FROM impacts WHERE status='pending' AND due_at <= now(UTC) ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 1`, flip to `to_status`, set `claimed_at`, commit. A not-yet-due `pending` row is **not** claimed.
  - `reclaim_stale_impacts(session, in_status, to_status, older_than_seconds) -> int` — resets `impacts` stuck in an in-flight status past the cutoff.

> Uses `SessionLocal` (real commits) + explicit cleanup, like Plan 3's `test_queue.py` — the savepoint `db_session` fixture can't demonstrate cross-connection SKIP LOCKED or a `due_at` filter across committed rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue_impacts.py
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import delete
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.queue import claim_due_impact, reclaim_stale_impacts


def _clear():
    with SessionLocal() as s:
        for m in (Impact, Resolution, Extraction, Detection, Statement, Source, Figure, User):
            s.execute(delete(m))
        s.commit()


@pytest.fixture
def clean_db():
    _clear(); yield; _clear()


def _impact(due_offset_seconds, status="pending"):
    """Create the FK chain and one impact whose due_at is now + offset. Returns impact id."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        f = Figure(name="F", type="individual", aliases=[], owner_id=None); s.add(f); s.flush()
        src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                     origin="manual", owner_id=None); s.add(src); s.flush()
        st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text="t", url=None,
                       provenance="primary", published_at=now, status="resolved"); s.add(st); s.flush()
        ex = Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                        confidence=0.5, evidence_quote="t", model="m", version="baseline"); s.add(ex); s.flush()
        r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity",
                       measurable=True); s.add(r); s.flush()
        imp = Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="5m",
                     event_at=now, due_at=now + timedelta(seconds=due_offset_seconds), status=status)
        s.add(imp); s.commit()
        return imp.id


def test_claims_due_but_not_future(clean_db):
    due_id = _impact(-60)      # due 1 min ago
    _impact(3600)              # due in 1h -> must NOT be claimed
    with SessionLocal() as s:
        claimed = claim_due_impact(s)
        assert claimed is not None and claimed.id == due_id and claimed.status == "measuring"
        assert claimed.claimed_at is not None
    with SessionLocal() as s:
        assert claim_due_impact(s) is None   # only the future one remains


def test_reclaim_stale_impacts(clean_db):
    iid = _impact(-60, status="measuring")
    with SessionLocal() as s:
        s.get(Impact, iid).claimed_at = datetime(2000, 1, 1, tzinfo=timezone.utc); s.commit()
    with SessionLocal() as s:
        assert reclaim_stale_impacts(s, "measuring", "pending", 300) == 1
    with SessionLocal() as s:
        assert s.get(Impact, iid).status == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_queue_impacts.py -v`
Expected: FAIL with `ImportError: cannot import name 'claim_due_impact'`.

- [ ] **Step 3: Add the impact due-claim**

Append to `src/bellwether/queue.py` (add the `Impact` import at the top):
```python
from bellwether.models.impact import Impact


def claim_due_impact(session: Session, to_status: str = "measuring") -> Impact | None:
    """Claim the oldest-due `pending` impact whose window has elapsed.

    Same lock-then-release discipline as claim_one, with an added `due_at <= now()`
    filter so future windows aren't claimed early.
    """
    now = datetime.now(timezone.utc)
    impact = session.execute(
        select(Impact)
        .where(Impact.status == "pending", Impact.due_at <= now)
        .order_by(Impact.due_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if impact is None:
        return None
    impact.status = to_status
    impact.claimed_at = now
    session.commit()
    return impact


def reclaim_stale_impacts(session: Session, in_status: str, to_status: str,
                          older_than_seconds: float) -> int:
    """Reset impacts stuck in an in-flight status past the cutoff (crash recovery)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Impact)
        .where(Impact.status == in_status, Impact.claimed_at < cutoff)
        .values(status=to_status, claimed_at=None)
    )
    session.commit()
    return result.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_queue_impacts.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/queue.py tests/test_queue_impacts.py
git commit -m "feat: impacts due-queue claim (claim_due_impact + reclaim_stale_impacts)"
```

---

### Task 9: Generalize the worker harness

**Files:**
- Modify: `src/bellwether/worker.py`
- Test: `tests/test_worker.py` (existing Plan-3 tests must stay green; no new tests needed unless a Plan-3 test referenced removed fields)

**Interfaces:**
- Produces: a generalized `Stage` dataclass — `name: str`, `claim_next: Callable[[Session], object | None]`, `reclaim: Callable[[Session, float], int]`, `process: Callable[[Session, object], None]`. `make_detect_stage`/`make_extract_stage` are rewrapped to provide `claim_next`/`reclaim` closures (behavior unchanged). `run_worker` calls `stage.claim_next(session)` and `stage.reclaim(session, seconds)` instead of hardcoding `claim_one`/`reclaim_stale`.

> This is a behavior-preserving refactor. Plan-3 tests build stages via `make_detect_stage`/`make_extract_stage` and call `stage.process(...)` / `run_worker(stage, …)` — none construct `Stage(...)` directly or read `stage.claim_from`/`claim_to`, so they should pass unchanged. The periodic-reclaim test patches `bellwether.worker.reclaim_stale`, which the rewrapped closures still call, so it also stays green.

- [ ] **Step 1: Replace `Stage`, the stage builders, and `run_worker`**

Replace lines from `@dataclass class Stage` through the end of `run_worker` in `src/bellwether/worker.py` with:
```python
@dataclass
class Stage:
    name: str
    claim_next: Callable[[Session], object | None]
    reclaim: Callable[[Session, float], int]
    process: Callable[[Session, object], None]


def make_detect_stage(detector: Detector, threshold: float) -> Stage:
    def process(session: Session, statement) -> None:
        result = detector.detect(statement.text)
        session.add(Detection(
            statement_id=statement.id, is_relevant=result.is_relevant, score=result.score,
            model=detector.model, version="baseline",
        ))
        statement.status = "detected" if (result.is_relevant and result.score >= threshold) else "irrelevant"
        statement.claimed_at = None
        session.commit()

    return Stage(
        name="detect",
        claim_next=lambda s: claim_one(s, "new", "detecting"),
        reclaim=lambda s, secs: reclaim_stale(s, "detecting", "new", secs),
        process=process,
    )


def make_extract_stage(extractor: Extractor) -> Stage:
    def process(session: Session, statement) -> None:
        try:
            result = extractor.extract(statement.text)
        except ExtractionParseError:
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        if not is_verbatim(result.evidence_quote, statement.text):
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        session.add(Extraction(
            statement_id=statement.id, entities=result.entities, direction=result.direction,
            magnitude=result.magnitude, confidence=result.confidence,
            evidence_quote=result.evidence_quote, model=extractor.model, version="baseline",
        ))
        statement.status = "extracted"
        statement.claimed_at = None
        session.commit()

    return Stage(
        name="extract",
        claim_next=lambda s: claim_one(s, "detected", "extracting"),
        reclaim=lambda s, secs: reclaim_stale(s, "extracting", "detected", secs),
        process=process,
    )


def run_worker(stage: Stage, *, session_factory=SessionLocal, poll_interval=None,
               reclaim_interval_seconds: float | None = None,
               once: bool = False, stop_event: "threading.Event | None" = None) -> int:
    settings = get_settings()
    if poll_interval is None:
        poll_interval = settings.worker_poll_interval_seconds
    if reclaim_interval_seconds is None:
        reclaim_interval_seconds = settings.worker_stale_reclaim_seconds

    with session_factory() as session:
        stage.reclaim(session, settings.worker_stale_reclaim_seconds)
    last_reclaim = time.monotonic()

    processed = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if time.monotonic() - last_reclaim >= reclaim_interval_seconds:
            with session_factory() as session:
                stage.reclaim(session, settings.worker_stale_reclaim_seconds)
            last_reclaim = time.monotonic()
        with session_factory() as session:
            row = stage.claim_next(session)
            if row is not None:
                try:
                    stage.process(session, row)
                    processed += 1
                except Exception:
                    session.rollback()
                    logger.exception("stage %s failed for row id=%s",
                                     stage.name, getattr(row, "id", "?"))
        if row is None:
            if once:
                break
            if stop_event is not None:
                if stop_event.wait(poll_interval):
                    break
            else:
                time.sleep(poll_interval)
    return processed
```

- [ ] **Step 2: Run the existing worker suite to verify it still passes**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS (all Plan-3 worker tests green — the refactor is behavior-preserving). If any test fails because it referenced a removed `Stage` field, update that test to build the stage via `make_detect_stage`/`make_extract_stage` (the supported constructor).

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker.py
git commit -m "refactor: generalize worker Stage to claim_next/reclaim callables"
```

---

### Task 10: Resolve stage

**Files:**
- Modify: `src/bellwether/worker.py` (add `make_resolve_stage` + imports)
- Test: `tests/test_worker_resolve_measure.py`

**Interfaces:**
- Consumes: `claim_one`/`reclaim_stale` (Plan 3), `Extraction`/`Figure`/`Resolution`/`EntitySymbol`/`Impact` models, `Resolver`/`ResolveContext`/`ResolutionOutcome` + `normalize_entity`, `parse_windows`.
- Produces: `make_resolve_stage(resolver: Resolver, windows: list[tuple[str, timedelta]]) -> Stage` — claims `extracted → resolving`. `process`: loads the statement's `Extraction`; for each entity, uses the `entity_symbols` cache (hit → reuse; miss → `resolver.resolve(...)` then cache-insert, tolerating a concurrent unique clash); writes a `Resolution`; for each **measurable** resolution inserts one `pending` `Impact` per window (`event_at=published_at`, `due_at=published_at+delta`). Sets statement `resolved`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker_resolve_measure.py
from datetime import datetime, timezone
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact
from bellwether.llm.contracts import ResolutionOutcome
from bellwether.windows import parse_windows
from bellwether.worker import make_resolve_stage

WINDOWS = parse_windows("5m,1h,1d")


class StubResolver:
    model = "stub/resolve"
    def __init__(self, outcomes, record=None):
        self._outcomes = outcomes           # {entity: ResolutionOutcome}
        self.calls = record if record is not None else []
    def resolve(self, entity, context):
        self.calls.append(entity)
        return self._outcomes[entity]


def _extracted(db_session, entities):
    f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                 origin="manual", owner_id=None); db_session.add(src); db_session.flush()
    st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text="Tesla will grow.",
                   url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc), status="resolving")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=entities, direction="up", magnitude="small",
                    confidence=0.5, evidence_quote="Tesla", model="m", version="baseline")
    db_session.add(ex); db_session.flush()
    return st, ex


def test_resolve_writes_rows_and_pending_impacts(db_session):
    st, ex = _extracted(db_session, ["Tesla", "monetary policy"])
    outcomes = {
        "Tesla": ResolutionOutcome("TSLA", "equity", True, "Tesla Inc", 0.9),
        "monetary policy": ResolutionOutcome(None, None, False, None, None),
    }
    stage = make_resolve_stage(StubResolver(outcomes), WINDOWS)
    stage.process(db_session, st)

    assert st.status == "resolved" and st.claimed_at is None
    res = db_session.execute(select(Resolution).where(Resolution.extraction_id == ex.id)).scalars().all()
    assert {r.entity: r.measurable for r in res} == {"Tesla": True, "monetary policy": False}
    # 3 pending impacts for the measurable entity only, with correct due_at
    imps = db_session.execute(select(Impact)).scalars().all()
    assert len(imps) == 3
    assert {i.window for i in imps} == {"5m", "1h", "1d"}
    assert all(i.status == "pending" and i.symbol == "TSLA" for i in imps)
    assert all(i.event_at == st.published_at and i.due_at > i.event_at for i in imps)
    # entity_symbols cache populated for both (yes and no)
    assert db_session.execute(select(func.count()).select_from(EntitySymbol)).scalar_one() == 2


def test_resolve_uses_cache_on_second_entity(db_session):
    # pre-seed the cache; the resolver must NOT be called
    db_session.add(EntitySymbol(normalized_entity="tesla", symbol="TSLA", asset_class="equity",
                                measurable=True, instrument_name="Tesla Inc", confidence=0.9, source="llm"))
    db_session.flush()
    st, ex = _extracted(db_session, ["Tesla"])
    calls = []
    stage = make_resolve_stage(StubResolver({}, record=calls), WINDOWS)
    stage.process(db_session, st)
    assert calls == []  # cache hit — resolver never invoked
    assert db_session.execute(select(func.count()).select_from(Impact)).scalar_one() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worker_resolve_measure.py -v`
Expected: FAIL with `ImportError: cannot import name 'make_resolve_stage'`.

- [ ] **Step 3: Add the resolve stage**

Add these imports at the top of `src/bellwether/worker.py`:
```python
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from bellwether.models.figure import Figure
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact
from bellwether.llm.contracts import Resolver, ResolveContext, ResolutionOutcome
from bellwether.llm.resolve import normalize_entity
```
Then add:
```python
def _cached_outcome(session: Session, entity: str) -> ResolutionOutcome | None:
    row = session.execute(
        select(EntitySymbol).where(EntitySymbol.normalized_entity == normalize_entity(entity))
    ).scalar_one_or_none()
    if row is None:
        return None
    return ResolutionOutcome(row.symbol, row.asset_class, row.measurable,
                             row.instrument_name, row.confidence)


def make_resolve_stage(resolver: Resolver, windows: list[tuple[str, "timedelta"]]) -> Stage:
    def process(session: Session, statement) -> None:
        extraction = session.execute(
            select(Extraction).where(Extraction.statement_id == statement.id)
        ).scalar_one_or_none()
        entities = list(extraction.entities) if extraction is not None else []
        figure = session.get(Figure, statement.figure_id)
        snippet = statement.text[:200]

        for entity in entities:
            outcome = _cached_outcome(session, entity)
            if outcome is None:
                outcome = resolver.resolve(
                    entity, ResolveContext(figure_name=figure.name if figure else "", snippet=snippet)
                )
                session.add(EntitySymbol(
                    normalized_entity=normalize_entity(entity), symbol=outcome.symbol,
                    asset_class=outcome.asset_class, measurable=outcome.measurable,
                    instrument_name=outcome.instrument_name, confidence=outcome.confidence, source="llm",
                ))
                try:
                    session.flush()
                except IntegrityError:  # concurrent worker cached it first
                    session.rollback()
                    outcome = _cached_outcome(session, entity) or outcome

            resolution = Resolution(
                extraction_id=extraction.id, entity=entity, symbol=outcome.symbol,
                asset_class=outcome.asset_class, measurable=outcome.measurable,
            )
            session.add(resolution)
            session.flush()  # need resolution.id for the impacts

            if outcome.measurable:
                for name, delta in windows:
                    session.add(Impact(
                        resolution_id=resolution.id, symbol=outcome.symbol,
                        asset_class=outcome.asset_class, window=name,
                        event_at=statement.published_at, due_at=statement.published_at + delta,
                        status="pending",
                    ))

        statement.status = "resolved"
        statement.claimed_at = None
        session.commit()

    return Stage(
        name="resolve",
        claim_next=lambda s: claim_one(s, "extracted", "resolving"),
        reclaim=lambda s, secs: reclaim_stale(s, "resolving", "extracted", secs),
        process=process,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_worker_resolve_measure.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker_resolve_measure.py
git commit -m "feat: resolve stage (cache + resolutions + pending impacts)"
```

---

### Task 11: Measure stage + CLI + end-to-end

**Files:**
- Modify: `src/bellwether/worker.py` (add `make_measure_stage`, wire `resolve`/`measure` into `_build_stage` + CLI)
- Test: `tests/test_worker_resolve_measure.py` (append measure + e2e tests)

**Interfaces:**
- Consumes: `claim_due_impact`/`reclaim_stale_impacts` (Task 8), `MarketData`/`MarketDataError` (Task 4), `compute_impact` (Task 5), `parse_window` (Task 3), `build_resolver`/`build_market_data`/`parse_windows`.
- Produces: `make_measure_stage(market: MarketData, baseline_bars: int) -> Stage` — claim = `claim_due_impact`; `process`: fetch the price series around `event_at` via `market.price_series` (a `MarketDataError` propagates → reclaim-retry), run `compute_impact`; `None` → terminal `measure_failed`; a point → fill fields + `measured`. CLI gains `resolve`/`measure`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker_resolve_measure.py`:
```python
import pytest
from datetime import timedelta
from sqlalchemy import delete
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.models.detection import Detection
from bellwether.market.base import PriceSeries, PriceBar, MarketDataError
from bellwether.worker import make_measure_stage, make_resolve_stage, run_worker


class StubMarket:
    def __init__(self, series=None, exc=None): self._series, self._exc = series, exc
    def lookup(self, symbol, asset_class): return None
    def search(self, query): return []
    def price_series(self, symbol, asset_class, start, end, window):
        if self._exc is not None:
            raise self._exc
        return self._series


def _pending_impact(db_session):
    st, ex = _extracted(db_session, ["Tesla"])
    st.status = "resolved"
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    t0 = st.published_at
    imp = Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="5m",
                 event_at=t0, due_at=t0 + timedelta(minutes=5), status="measuring")  # pre-claimed
    db_session.add(imp); db_session.flush()
    return imp, t0


def test_measure_fills_impact(db_session):
    imp, t0 = _pending_impact(db_session)
    series = PriceSeries(bars=[
        PriceBar(ts=t0, price=100.0, volume=10.0),
        PriceBar(ts=t0 + timedelta(minutes=5), price=110.0, volume=50.0),
    ])
    make_measure_stage(StubMarket(series=series), baseline_bars=20).process(db_session, imp)
    assert imp.status == "measured" and imp.claimed_at is None and imp.measured_at is not None
    assert imp.price_t0 == 100.0 and imp.price_after == 110.0
    assert abs(imp.pct_move - 0.10) < 1e-9


def test_measure_insufficient_data_is_terminal(db_session):
    imp, t0 = _pending_impact(db_session)
    make_measure_stage(StubMarket(series=PriceSeries(bars=[])), baseline_bars=20).process(db_session, imp)
    assert imp.status == "measure_failed"


def test_measure_transient_error_propagates(db_session):
    imp, t0 = _pending_impact(db_session)
    stage = make_measure_stage(StubMarket(exc=MarketDataError("timeout")), baseline_bars=20)
    with pytest.raises(MarketDataError):
        stage.process(db_session, imp)
    assert imp.status == "measuring"  # left for reclaim-retry, not burned


def test_end_to_end_resolve_then_measure():
    # real Postgres + SessionLocal; past-dated statement so all windows are already due
    def _clear():
        with SessionLocal() as s:
            for m in (Impact, Resolution, Extraction, Detection, Statement, Source, Figure, User):
                s.execute(delete(m))
            s.commit()
    _clear()
    try:
        with SessionLocal() as s:
            f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=None); s.add(f); s.flush()
            src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                         origin="manual", owner_id=None); s.add(src); s.flush()
            t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # well in the past -> due
            st = Statement(figure_id=f.id, source_id=src.id, external_id="e2e", text="Tesla will grow.",
                           url=None, provenance="primary", published_at=t0, status="extracted"); s.add(st); s.flush()
            s.add(Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                             confidence=0.5, evidence_quote="Tesla", model="m", version="baseline"))
            s.commit()
        resolver = StubResolver({"Tesla": ResolutionOutcome("TSLA", "equity", True, "Tesla Inc", 0.9)})
        assert run_worker(make_resolve_stage(resolver, WINDOWS), once=True) == 1
        series = PriceSeries(bars=[
            PriceBar(ts=t0, price=100.0, volume=10.0),
            PriceBar(ts=t0 + timedelta(days=1), price=90.0, volume=10.0),
        ])
        measured = run_worker(make_measure_stage(StubMarket(series=series), baseline_bars=20), once=True)
        assert measured == 3  # 5m/1h/1d all due
        with SessionLocal() as s:
            statuses = {i.window: i.status for i in s.execute(select(Impact)).scalars()}
            assert statuses["1d"] == "measured"
    finally:
        _clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_worker_resolve_measure.py -k "measure or end_to_end" -v`
Expected: FAIL with `ImportError: cannot import name 'make_measure_stage'`.

- [ ] **Step 3: Add the measure stage + CLI wiring**

Add imports at the top of `src/bellwether/worker.py`:
```python
from bellwether.queue import claim_due_impact, reclaim_stale_impacts
from bellwether.market.base import MarketData, MarketDataError
from bellwether.measure.impact import compute_impact
from bellwether.windows import parse_window, parse_windows
from bellwether.llm.resolve import build_resolver
from bellwether.market.registry import build_market_data
from datetime import datetime, timezone
```
Add the stage:
```python
def make_measure_stage(market: MarketData, baseline_bars: int) -> Stage:
    def process(session: Session, impact) -> None:
        window_delta = parse_window(impact.window)
        # Fetch a series that brackets the event and its window, with lookback for the baseline.
        start = impact.event_at - timedelta(days=1)
        end = impact.due_at + timedelta(seconds=1)
        series = market.price_series(impact.symbol, impact.asset_class, start, end, impact.window)
        # (a MarketDataError from the adapter propagates -> run_worker rollback -> reclaim retry)
        point = compute_impact(series, impact.event_at, window_delta, baseline_bars)
        if point is None:
            impact.status = "measure_failed"  # insufficient free data — terminal
            impact.claimed_at = None
            session.commit()
            return
        impact.price_t0 = point.price_t0
        impact.price_after = point.price_after
        impact.pct_move = point.pct_move
        impact.volume_spike = point.volume_spike
        impact.status = "measured"
        impact.measured_at = datetime.now(timezone.utc)
        impact.claimed_at = None
        session.commit()

    return Stage(
        name="measure",
        claim_next=lambda s: claim_due_impact(s),
        reclaim=lambda s, secs: reclaim_stale_impacts(s, "measuring", "pending", secs),
        process=process,
    )
```
Replace `_build_stage` and the CLI `choices` to include the new stages:
```python
def _build_stage(name: str) -> Stage:
    settings = get_settings()
    if name == "detect":
        return make_detect_stage(build_detector(), settings.relevance_threshold)
    if name == "extract":
        return make_extract_stage(build_extractor())
    if name == "resolve":
        return make_resolve_stage(build_resolver(), parse_windows(settings.measure_windows))
    return make_measure_stage(build_market_data(), settings.measure_baseline_bars)
```
In `main`, change the argparse choices line to:
```python
    parser.add_argument("stage", choices=["detect", "extract", "resolve", "measure"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker_resolve_measure.py -v`
Expected: PASS (all resolve + measure + e2e tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (Plans 1–4), output pristine.

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker_resolve_measure.py
git commit -m "feat: measure stage + resolve/measure CLI + end-to-end"
```

---

## Self-Review

**Spec coverage (Plan 4's slice):**
- Resolve = LLM propose → yfinance verify → self-building cache — Tasks 4, 6, 7, 10 ✓ (bounded loop, deterministic verify gate, `entity_symbols` cache).
- `resolutions`/`entity_symbols`/`impacts` tables (shared corpus) + status machine — Tasks 1, 10, 11 ✓.
- Measure due-queue (impacts `pending`+`due_at`, `SKIP LOCKED … AND due_at<=now()`) — Tasks 8, 11 ✓.
- Pure impact math (pct_move + volume_spike) on synthetic series — Task 5 ✓.
- Market adapter behind one interface; parse tested, no live network — Tasks 4, 6 ✓.
- Terminal (`measure_failed`) vs retryable (transient `MarketDataError` propagates) — Task 11 ✓; adapter behind a paradigm-agnostic exception, stage layer imports no yfinance internals — Tasks 4, 6, 11 ✓.
- Worker harness generalized (one loop for status-claim + due-claim stages) — Task 9 ✓; CLI `resolve`/`measure` — Task 11 ✓.
- Provider-agnostic Resolve config — Task 2, 7 ✓.
- Firewall respected by construction; the metric-path assertion is Plan 5 — noted (no task, correct).

**Deliberate scope decisions (flag for the reviewer/user):**
- **CoinGecko deferred.** The spec (§6) lists yfinance + CoinGecko; this plan ships **yfinance only** for v1 (it serves crypto via `X-USD` pairs) and keeps the registry's `asset_class` seam so CoinGecko can be added later behind the same `MarketData` interface. This narrows the spec to reduce a second external API. If crypto-via-CoinGecko is wanted in Plan 4, add a task; otherwise it's deferred with intent.
- **Adapter network methods aren't unit-tested** (only the pure `_series_from_history` parser is) — consistent with "no live network in tests." The real yfinance calls are exercised in manual/live runs, not the suite.

**Placeholder scan:** every code step shows complete code; every command has expected output. The only calibration note (Task 7) is the installed `DummyLM` answer-shape — the asserted behavior is fully specified.

**Type consistency:** `ResolutionOutcome(symbol, asset_class, measurable, instrument_name, confidence)` is defined in Task 7 and consumed identically in Tasks 10/11 and the stubs. `PriceSeries(bars=[PriceBar(ts, price, volume)])` and `ImpactPoint(price_t0, price_after, pct_move, volume_spike)` are defined in Tasks 4/5 and used in 5/6/11. `MarketData`/`SymbolVerifier` methods (`lookup`, `search`, `price_series`) match the stubs and adapter. `Stage(name, claim_next, reclaim, process)` (Task 9) is constructed by all `make_*_stage` builders and consumed by `run_worker`. `claim_due_impact`/`reclaim_stale_impacts` (Task 8) match their call sites in Task 11. Status strings (`resolving`/`resolved`/`pending`/`measuring`/`measured`/`measure_failed`) are consistent across the stages, the queue, and the tests. Window names (`5m`/`1h`/`1d`) map through `parse_window`/`parse_windows` consistently.
