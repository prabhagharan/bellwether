# bellwether Plan 3 — LLM Layer (Detect + Extract) & Queue Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn ingested `statements` into structured market signals via two DSPy LLM stages — Detect (cheap relevance gate) and Extract (structured signal) — driven by a generic Postgres `FOR UPDATE SKIP LOCKED` worker/queue harness, with a code-enforced verbatim-substring guard on every extracted quote.

**Architecture:** Builds on Plan 1 (FastAPI + Postgres + JWT) and Plan 2 (ingestion + `statements` with a `status` column). Adds two shared-corpus tables (`detections`, `extractions`) and a `statements.claimed_at` column; a generic queue harness (`claim_one` / `reclaim_stale`); DSPy Detect (`Predict`) and Extract (`ChainOfThought`) modules behind **frozen `Detector`/`Extractor` contracts** with `build_*()` factories (so the DSPy paradigm is swappable later without touching downstream code); a verbatim-substring guard applied at the **stage boundary** (outside the swappable module); and a worker runtime (`run_worker` loop + CLI) that claims → processes → advances `status`. Baseline/un-optimized only (`version="baseline"`); optimization is Plan 5.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 (sync), Alembic, Postgres (psycopg 3, JSONB, `FOR UPDATE SKIP LOCKED`), **DSPy** (model-agnostic via LiteLLM), pytest. Design spec: `docs/superpowers/specs/2026-07-05-bellwether-03-llm-layer-design.md`.

## Global Constraints

- Python **3.11+**. Postgres only via `postgresql+psycopg://` (psycopg 3). SQLAlchemy **2.0** sync (`Mapped`, `mapped_column`). JSONB via `sqlalchemy.dialects.postgresql.JSONB`.
- **Shared corpus:** `detections` and `extractions` carry **NO `owner_id`** (symmetric with `statements` — the future subscription model treats detection/extraction rows as global).
- **DSPy-only, provider-agnostic:** every LLM stage is a **single call**. No LangChain/LangGraph, no `dspy.ReAct` / multi-step agent (deferred, parent spec §18). Models are selected by config strings (`detect_model`/`extract_model`); LiteLLM reads whatever credential the chosen provider needs from the environment (e.g. `ANTHROPIC_API_KEY` for the default Claude models) — **no provider key is modeled in `Settings`**.
- **Verbatim-substring guard:** a non-substring `evidence_quote` is rejected in code — no `extractions` row is written and the statement goes to `extract_failed`. Enforced at the stage boundary, **outside** the DSPy module, so any future paradigm swap still cannot emit a fabricated quote.
- **Baseline only:** `version="baseline"` on every row. No `dspy.compile`, golden sets, champion/challenger, or eval tables (Plan 5).
- **Status vocabulary:** `new → detecting → {detected | irrelevant}`, then `detected → extracting → {extracted | extract_failed}`. `detecting`/`extracting` are transient in-flight markers; `reclaim_stale` resets rows stuck in them.
- **Tests use a REAL Postgres (no DB mocking) and NO live network.** The LLM is replaced by an injected fake in every test: DSPy **module** tests use `dspy.utils.DummyLM`; **stage / worker / e2e** tests inject a plain-Python stub `Detector`/`Extractor` (no DSPy, no network).
- **ENVIRONMENT:** the shell's `python`/`pytest`/`alembic` are shadowed by a MacPorts python3.12 — run everything via `.venv/bin/python -m …` (and `.venv/bin/alembic …`). Postgres up via `docker compose up -d`; `.env` exists (git-ignored).

## File Structure

```
src/bellwether/
├── models/
│   ├── __init__.py            # (modify) register Detection, Extraction
│   ├── detection.py           # Detection model
│   ├── extraction.py          # Extraction model
│   └── statement.py           # (modify) add claimed_at column
├── config.py                  # (modify) add model/threshold/worker settings
├── queue.py                   # claim_one + reclaim_stale (generic, stage-agnostic)
├── worker.py                  # Stage + make_*_stage + run_worker loop + CLI (__main__)
└── llm/
    ├── __init__.py            # (empty)
    ├── config.py              # make_lm(model) -> dspy.LM
    ├── contracts.py           # DetectionResult, ExtractionResult, Detector, Extractor
    ├── guard.py               # is_verbatim(quote, source_text)
    ├── detect.py              # DetectSig + Detect module + build_detector()
    └── extract.py             # ExtractSig + Extract module + build_extractor()
migrations/versions/           # ONE new migration: detections, extractions, statements.claimed_at
tests/
├── test_models_llm.py         # Detection/Extraction columns (no owner_id)
├── test_config_llm.py         # new settings defaults
├── test_queue.py              # claim_one / reclaim_stale (real Postgres, SessionLocal)
├── test_worker.py             # stages + run_worker + e2e (stub detector/extractor)
└── llm/
    ├── __init__.py            # (empty)
    ├── test_guard.py
    ├── test_detect.py         # DummyLM
    └── test_extract.py        # DummyLM
```

---

### Task 1: Models + migration (detections, extractions, statements.claimed_at)

**Files:**
- Create: `src/bellwether/models/detection.py`, `src/bellwether/models/extraction.py`
- Modify: `src/bellwether/models/statement.py` (add `claimed_at`), `src/bellwether/models/__init__.py`
- Create: the generated migration under `migrations/versions/`
- Test: `tests/test_models_llm.py`

**Interfaces:**
- Consumes: `Base` (`bellwether.models.base`), `Statement` (FK target).
- Produces:
  - `Detection` (`detections`): `id` PK, `statement_id` FK→statements.id (CASCADE, indexed), `is_relevant` bool, `score` float, `model` str, `version` str, `created_at` tz datetime server default now(). **No `owner_id`.**
  - `Extraction` (`extractions`): `id` PK, `statement_id` FK→statements.id (CASCADE, indexed), `entities` JSONB (list, default `[]`), `direction` str, `magnitude` str, `confidence` float, `evidence_quote` Text, `model` str, `version` str, `created_at`. **No `owner_id`.**
  - `Statement.claimed_at`: nullable tz datetime (new column).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_llm.py
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.models.statement import Statement


def test_detection_columns():
    cols = set(Detection.__table__.columns.keys())
    assert {"id", "statement_id", "is_relevant", "score", "model", "version", "created_at"} <= cols
    assert "owner_id" not in cols  # shared corpus


def test_extraction_columns():
    cols = set(Extraction.__table__.columns.keys())
    assert {"id", "statement_id", "entities", "direction", "magnitude",
            "confidence", "evidence_quote", "model", "version", "created_at"} <= cols
    assert "owner_id" not in cols  # shared corpus


def test_statement_has_claimed_at():
    assert "claimed_at" in Statement.__table__.columns.keys()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.models.detection`.

- [ ] **Step 3: Write the Detection model**

```python
# src/bellwether/models/detection.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="baseline")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Write the Extraction model**

```python
# src/bellwether/models/extraction.py
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    magnitude: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="baseline")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: Add `claimed_at` to the Statement model**

In `src/bellwether/models/statement.py`, add this column after the `status` line (keep everything else unchanged):
```python
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 6: Register the new models for Alembic**

```python
# src/bellwether/models/__init__.py
from bellwether.models.base import Base
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction

__all__ = ["Base", "User", "Figure", "Source", "Statement", "Detection", "Extraction"]
```

- [ ] **Step 7: Run the model test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models_llm.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Generate and apply the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "create detections, extractions; add statements.claimed_at"
.venv/bin/alembic upgrade head
```
Expected: a new version file is created and `upgrade head` completes. Open the generated file and confirm it (a) creates `detections` and `extractions` with their FKs (CASCADE) and `statement_id` indexes, and (b) adds the `claimed_at` column to `statements`. Do not hand-edit beyond what autogenerate produced.

- [ ] **Step 9: Commit**

```bash
git add src/bellwether/models tests/test_models_llm.py migrations
git commit -m "feat: detections/extractions models + statements.claimed_at migration"
```

---

### Task 2: Config — LLM & worker settings

**Files:**
- Modify: `src/bellwether/config.py`, `.env.example`
- Test: `tests/test_config_llm.py`

**Interfaces:**
- Produces on `Settings` (all with defaults, so existing `.env` still loads): `detect_model: str = "anthropic/claude-haiku-4-5"`, `extract_model: str = "anthropic/claude-sonnet-5"`, `relevance_threshold: float = 0.5`, `worker_poll_interval_seconds: float = 5.0`, `worker_stale_reclaim_seconds: float = 300.0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_llm.py
from bellwether.config import Settings


def test_llm_and_worker_defaults():
    s = Settings(
        database_url="postgresql+psycopg://x/y",
        jwt_secret="s",
        admin_username="a",
        admin_password="b",
    )
    assert s.detect_model == "anthropic/claude-haiku-4-5"
    assert s.extract_model == "anthropic/claude-sonnet-5"
    assert s.relevance_threshold == 0.5
    assert s.worker_poll_interval_seconds == 5.0
    assert s.worker_stale_reclaim_seconds == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_llm.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'detect_model'`.

- [ ] **Step 3: Add the settings**

In `src/bellwether/config.py`, add these fields inside `class Settings` after `admin_password`:
```python
    detect_model: str = "anthropic/claude-haiku-4-5"
    extract_model: str = "anthropic/claude-sonnet-5"
    relevance_threshold: float = 0.5
    worker_poll_interval_seconds: float = 5.0
    worker_stale_reclaim_seconds: float = 300.0
```

- [ ] **Step 4: Document the knobs in `.env.example`**

Append to `.env.example`:
```bash
# --- LLM layer (Plan 3) ---
# Model strings are provider-agnostic (DSPy via LiteLLM). Change the provider by
# changing the model string AND setting that provider's own credential env var —
# e.g. ANTHROPIC_API_KEY for the default Claude models, OPENAI_API_KEY for OpenAI,
# OPENROUTER_API_KEY for openrouter/* models. The key is NOT a bellwether setting.
# DETECT_MODEL=anthropic/claude-haiku-4-5
# EXTRACT_MODEL=anthropic/claude-sonnet-5
# RELEVANCE_THRESHOLD=0.5
# WORKER_POLL_INTERVAL_SECONDS=5
# WORKER_STALE_RECLAIM_SECONDS=300
# Set the credential your chosen models' provider needs, e.g.:
# ANTHROPIC_API_KEY=sk-ant-...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_llm.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/config.py .env.example tests/test_config_llm.py
git commit -m "feat: LLM + worker settings (provider-agnostic model knobs)"
```

---

### Task 3: Verbatim-substring guard

**Files:**
- Create: `src/bellwether/llm/__init__.py` (empty), `src/bellwether/llm/guard.py`
- Test: `tests/llm/__init__.py` (empty), `tests/llm/test_guard.py`

**Interfaces:**
- Produces: `is_verbatim(quote: str, source_text: str) -> bool` — `True` iff `quote` is a non-empty literal substring of `source_text`. An empty/whitespace-only quote is `False`.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_guard.py
from bellwether.llm.guard import is_verbatim

SOURCE = "The central bank will raise rates. Inflation remains elevated."


def test_exact_substring_passes():
    assert is_verbatim("will raise rates", SOURCE) is True


def test_non_substring_fails():
    assert is_verbatim("will cut rates", SOURCE) is False


def test_empty_or_whitespace_fails():
    assert is_verbatim("", SOURCE) is False
    assert is_verbatim("   ", SOURCE) is False


def test_full_text_passes():
    assert is_verbatim(SOURCE, SOURCE) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.llm.guard`.

- [ ] **Step 3: Write the guard**

Create empty `src/bellwether/llm/__init__.py` and empty `tests/llm/__init__.py`, then:
```python
# src/bellwether/llm/guard.py
def is_verbatim(quote: str, source_text: str) -> bool:
    """True iff `quote` is a non-empty literal substring of `source_text`.

    The structural anti-fabrication guarantee: an extracted evidence_quote that is
    not a verbatim substring of the original statement is rejected in code.
    """
    if quote is None or not quote.strip():
        return False
    return quote in source_text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_guard.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/llm/__init__.py src/bellwether/llm/guard.py tests/llm/__init__.py tests/llm/test_guard.py
git commit -m "feat: verbatim-substring guard (anti-fabrication)"
```

---

### Task 4: LLM contracts (frozen Detector/Extractor seam)

**Files:**
- Create: `src/bellwether/llm/contracts.py`
- Test: `tests/llm/test_contracts.py`

**Interfaces:**
- Produces:
  - `DetectionResult` — frozen dataclass: `is_relevant: bool`, `score: float`.
  - `ExtractionResult` — frozen dataclass: `entities: list[str]`, `direction: str`, `magnitude: str`, `confidence: float`, `evidence_quote: str`.
  - `Detector` — `runtime_checkable` Protocol: attribute `model: str`, method `detect(self, statement_text: str) -> DetectionResult`.
  - `Extractor` — `runtime_checkable` Protocol: attribute `model: str`, method `extract(self, statement_text: str) -> ExtractionResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_contracts.py
from bellwether.llm.contracts import DetectionResult, ExtractionResult, Detector, Extractor


def test_detection_result_holds_fields():
    r = DetectionResult(is_relevant=True, score=0.9)
    assert r.is_relevant is True and r.score == 0.9


def test_extraction_result_holds_fields():
    r = ExtractionResult(entities=["TSLA"], direction="up", magnitude="moderate",
                         confidence=0.8, evidence_quote="q")
    assert r.entities == ["TSLA"] and r.direction == "up" and r.evidence_quote == "q"


def test_stub_satisfies_protocols():
    class StubDetector:
        model = "stub/detect"
        def detect(self, statement_text): return DetectionResult(True, 1.0)

    class StubExtractor:
        model = "stub/extract"
        def extract(self, statement_text):
            return ExtractionResult([], "neutral", "none", 0.0, "")

    assert isinstance(StubDetector(), Detector)
    assert isinstance(StubExtractor(), Extractor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.llm.contracts`.

- [ ] **Step 3: Write the contracts**

```python
# src/bellwether/llm/contracts.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DetectionResult:
    is_relevant: bool
    score: float


@dataclass(frozen=True)
class ExtractionResult:
    entities: list[str]
    direction: str
    magnitude: str
    confidence: float
    evidence_quote: str


@runtime_checkable
class Detector(Protocol):
    model: str
    def detect(self, statement_text: str) -> DetectionResult: ...


@runtime_checkable
class Extractor(Protocol):
    model: str
    def extract(self, statement_text: str) -> ExtractionResult: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_contracts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/llm/contracts.py tests/llm/test_contracts.py
git commit -m "feat: frozen Detector/Extractor contracts (swappable-paradigm seam)"
```

---

### Task 5: Generic queue harness (`claim_one` / `reclaim_stale`)

**Files:**
- Create: `src/bellwether/queue.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: `Statement` (Task 1, now with `status` + `claimed_at`), `Session`.
- Produces:
  - `claim_one(session, from_status, to_status) -> Statement | None` — selects the oldest (`published_at`) statement in `from_status` using `FOR UPDATE SKIP LOCKED`, flips its `status` to `to_status`, sets `claimed_at = now(UTC)`, **commits**, and returns it (or `None` if none available).
  - `reclaim_stale(session, in_status, to_status, older_than_seconds) -> int` — resets rows in `in_status` whose `claimed_at` is older than the cutoff back to `to_status` (clears `claimed_at`), commits, returns the count reset.

> **Why these tests use `SessionLocal` and not the `db_session` fixture:** the `db_session` fixture wraps a **single connection** in a savepoint transaction that is rolled back at teardown. `FOR UPDATE SKIP LOCKED` is about **two independent connections** racing for a row, and committed rows must be visible across connections — neither is possible inside one rolled-back transaction. So these tests commit real rows via `SessionLocal` and clean up explicitly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_queue.py
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import delete, select
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.queue import claim_one, reclaim_stale


def _clear():
    with SessionLocal() as s:
        for m in (Statement, Source, Figure, User):
            s.execute(delete(m))
        s.commit()


@pytest.fixture
def clean_db():
    _clear()
    yield
    _clear()


def _seed(n, status="new"):
    """Create n statements (oldest first) and return their ids in publish order."""
    with SessionLocal() as s:
        f = Figure(name="F", type="individual", aliases=[], owner_id=None)
        s.add(f); s.flush()
        src = Source(figure_id=f.id, connector_type="rss", config={},
                     provenance="primary", origin="manual", owner_id=None)
        s.add(src); s.flush()
        ids = []
        for i in range(n):
            st = Statement(
                figure_id=f.id, source_id=src.id, external_id=f"e{i}", text=f"t{i}",
                url=None, provenance="primary",
                published_at=datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
                status=status,
            )
            s.add(st); s.flush()
            ids.append(st.id)
        s.commit()
        return ids


def test_claim_one_takes_oldest_and_flips_status(clean_db):
    ids = _seed(2)  # ids[0] is oldest by published_at
    with SessionLocal() as s:
        claimed = claim_one(s, "new", "detecting")
        assert claimed is not None
        assert claimed.id == ids[0]
        assert claimed.status == "detecting"
        assert claimed.claimed_at is not None
    # a second claim takes the next row; a third finds nothing
    with SessionLocal() as s:
        second = claim_one(s, "new", "detecting")
        assert second.id == ids[1]
    with SessionLocal() as s:
        assert claim_one(s, "new", "detecting") is None


def test_claim_one_skips_locked_rows(clean_db):
    ids = _seed(1)
    sa = SessionLocal()
    # session A locks the only 'new' row with FOR UPDATE SKIP LOCKED, does NOT commit
    locked = sa.execute(
        select(Statement).where(Statement.status == "new")
        .with_for_update(skip_locked=True).limit(1)
    ).scalar_one()
    assert locked.id == ids[0]
    try:
        # session B must skip the locked row -> nothing to claim
        with SessionLocal() as sb:
            assert claim_one(sb, "new", "detecting") is None
    finally:
        sa.rollback(); sa.close()


def test_reclaim_stale_resets_old_in_flight_rows(clean_db):
    ids = _seed(1, status="detecting")
    # backdate claimed_at so it is older than the cutoff
    with SessionLocal() as s:
        st = s.get(Statement, ids[0])
        st.claimed_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        s.commit()
    with SessionLocal() as s:
        n = reclaim_stale(s, "detecting", "new", older_than_seconds=300)
        assert n == 1
    with SessionLocal() as s:
        st = s.get(Statement, ids[0])
        assert st.status == "new" and st.claimed_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.queue`.

- [ ] **Step 3: Write the queue harness**

```python
# src/bellwether/queue.py
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement


def claim_one(session: Session, from_status: str, to_status: str) -> Statement | None:
    """Claim the oldest statement in `from_status` and flip it to `to_status`.

    Uses FOR UPDATE SKIP LOCKED so concurrent workers never claim the same row. The
    short claim transaction commits immediately, releasing the row lock BEFORE the slow
    LLM work runs — a worker never holds a row lock across network I/O.
    """
    statement = session.execute(
        select(Statement)
        .where(Statement.status == from_status)
        .order_by(Statement.published_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if statement is None:
        return None
    statement.status = to_status
    statement.claimed_at = datetime.now(timezone.utc)
    session.commit()
    return statement


def reclaim_stale(session: Session, in_status: str, to_status: str,
                  older_than_seconds: float) -> int:
    """Reset rows stuck in an in-flight status past the cutoff back to `to_status`.

    Recovery for workers that crashed mid-process, leaving rows in `detecting` /
    `extracting`. Returns the number of rows reset.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Statement)
        .where(Statement.status == in_status, Statement.claimed_at < cutoff)
        .values(status=to_status, claimed_at=None)
    )
    session.commit()
    return result.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_queue.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/queue.py tests/test_queue.py
git commit -m "feat: generic FOR UPDATE SKIP LOCKED queue harness (claim_one + reclaim_stale)"
```

---

### Task 6: DSPy LM config + Detect module

**Files:**
- Create: `src/bellwether/llm/config.py`, `src/bellwether/llm/detect.py`
- Modify: `pyproject.toml` (add `dspy`)
- Test: `tests/llm/test_detect.py`

**Interfaces:**
- Consumes: `get_settings` (`bellwether.config`), `DetectionResult` (Task 4).
- Produces:
  - `config.make_lm(model: str) -> dspy.LM` — a LiteLLM-backed LM for `model`.
  - `detect.DetectSig` — `dspy.Signature`: `statement_text` → `is_relevant: bool`, `score: float`.
  - `detect.Detect(dspy.Module)` — wraps `dspy.Predict(DetectSig)`; `forward(statement_text)` returns a `dspy.Prediction` (the compile target for Plan 5).
  - `detect.build_detector(lm=None) -> Detector` — returns an adapter with `.model` (settings `detect_model`) and `.detect(statement_text) -> DetectionResult`, mapping the Prediction to the frozen contract. `lm` overrides the LM (tests pass a `DummyLM`).

- [ ] **Step 1: Add the dspy dependency**

In `pyproject.toml`, add `"dspy>=2.5",` to `[project].dependencies` (after `feedparser`), then install:
```bash
.venv/bin/python -m pip install -e ".[dev]"
```
Expected: `dspy` (and its LiteLLM dependency) install.

- [ ] **Step 2: Write the failing test**

```python
# tests/llm/test_detect.py
from dspy.utils import DummyLM
from bellwether.llm.detect import build_detector
from bellwether.llm.contracts import DetectionResult


def test_detect_maps_prediction_to_result():
    # DummyLM returns canned output-field values; no network, no provider key.
    lm = DummyLM([{"is_relevant": "True", "score": "0.87"}])
    detector = build_detector(lm=lm)
    result = detector.detect("The central bank will raise rates.")
    assert isinstance(result, DetectionResult)
    assert result.is_relevant is True
    assert result.score == 0.87
    assert isinstance(detector.model, str) and detector.model
```

> If Step 3's run shows a `DummyLM(...)` signature mismatch, the installed dspy version's `dspy.utils.DummyLM` expects a different answer shape — inspect `help(DummyLM)` and adjust the canned-answer literal (this is API calibration, not a logic change; the mapping under test is unchanged).

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.llm.detect`.

- [ ] **Step 4: Write the LM config**

```python
# src/bellwether/llm/config.py
import dspy


def make_lm(model: str) -> dspy.LM:
    """Build a LiteLLM-backed DSPy LM for `model` (e.g. 'anthropic/claude-haiku-4-5').

    Provider-agnostic: LiteLLM routes by the model prefix and reads the provider's own
    credential from the environment. No provider key is passed here.
    """
    return dspy.LM(model)
```

- [ ] **Step 5: Write the Detect module + adapter**

```python
# src/bellwether/llm/detect.py
import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import DetectionResult


class DetectSig(dspy.Signature):
    """Decide whether a public statement is market-relevant (could move asset prices)."""
    statement_text: str = dspy.InputField()
    is_relevant: bool = dspy.OutputField(desc="true if it could plausibly move markets")
    score: float = dspy.OutputField(desc="confidence 0.0-1.0 that it is market-relevant")


class Detect(dspy.Module):
    """Single-call relevance classifier (the Plan-5 compile target)."""

    def __init__(self):
        super().__init__()
        self.predict = dspy.Predict(DetectSig)

    def forward(self, statement_text: str) -> dspy.Prediction:
        return self.predict(statement_text=statement_text)


class _DetectorAdapter:
    """Maps the DSPy Prediction onto the frozen Detector contract."""

    def __init__(self, module: Detect, model: str):
        self._module = module
        self.model = model

    def detect(self, statement_text: str) -> DetectionResult:
        pred = self._module(statement_text=statement_text)
        return DetectionResult(is_relevant=bool(pred.is_relevant), score=float(pred.score))


def build_detector(lm: dspy.LM | None = None) -> _DetectorAdapter:
    settings = get_settings()
    module = Detect()
    module.set_lm(lm or make_lm(settings.detect_model))
    return _DetectorAdapter(module, settings.detect_model)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_detect.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/bellwether/llm/config.py src/bellwether/llm/detect.py tests/llm/test_detect.py
git commit -m "feat: DSPy Detect module (Predict) + provider-agnostic LM config"
```

---

### Task 7: Extract module

**Files:**
- Create: `src/bellwether/llm/extract.py`
- Test: `tests/llm/test_extract.py`

**Interfaces:**
- Consumes: `get_settings`, `make_lm` (Task 6), `ExtractionResult` (Task 4).
- Produces:
  - `extract.ExtractSig` — `dspy.Signature`: `statement_text` → `entities: list[str]`, `direction: Literal["up","down","neutral"]`, `magnitude: Literal["none","small","moderate","large"]`, `confidence: float`, `evidence_quote: str`.
  - `extract.Extract(dspy.Module)` — wraps `dspy.ChainOfThought(ExtractSig)` (single call; adds a reasoning field). `forward` returns a `dspy.Prediction`.
  - `extract.build_extractor(lm=None) -> Extractor` — adapter with `.model` (settings `extract_model`) and `.extract(statement_text) -> ExtractionResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_extract.py
from dspy.utils import DummyLM
from bellwether.llm.extract import build_extractor
from bellwether.llm.contracts import ExtractionResult


def test_extract_maps_prediction_to_result():
    lm = DummyLM([{
        "reasoning": "Tesla guidance is bullish.",
        "entities": '["TSLA"]',
        "direction": "up",
        "magnitude": "moderate",
        "confidence": "0.8",
        "evidence_quote": "Tesla will grow",
    }])
    extractor = build_extractor(lm=lm)
    result = extractor.extract("Tesla will grow next quarter.")
    assert isinstance(result, ExtractionResult)
    assert result.entities == ["TSLA"]
    assert result.direction == "up"
    assert result.magnitude == "moderate"
    assert result.confidence == 0.8
    assert result.evidence_quote == "Tesla will grow"
    assert isinstance(extractor.model, str) and extractor.model
```

> As in Task 6: if the run reports a `DummyLM` signature mismatch, inspect `help(DummyLM)` and adjust the canned-answer literal for the installed dspy version. `ChainOfThought` adds a `reasoning` output field, so the answer dict includes `"reasoning"`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.llm.extract`.

- [ ] **Step 3: Write the Extract module + adapter**

```python
# src/bellwether/llm/extract.py
from typing import Literal
import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import ExtractionResult


class ExtractSig(dspy.Signature):
    """Extract a structured market signal from a public statement."""
    statement_text: str = dspy.InputField()
    entities: list[str] = dspy.OutputField(desc="tickers/companies/sectors named")
    direction: Literal["up", "down", "neutral"] = dspy.OutputField()
    magnitude: Literal["none", "small", "moderate", "large"] = dspy.OutputField()
    confidence: float = dspy.OutputField(desc="0.0-1.0")
    evidence_quote: str = dspy.OutputField(desc="a verbatim substring of statement_text")


class Extract(dspy.Module):
    """Single-call structured extractor (ChainOfThought adds a reasoning field only)."""

    def __init__(self):
        super().__init__()
        self.predict = dspy.ChainOfThought(ExtractSig)

    def forward(self, statement_text: str) -> dspy.Prediction:
        return self.predict(statement_text=statement_text)


class _ExtractorAdapter:
    def __init__(self, module: Extract, model: str):
        self._module = module
        self.model = model

    def extract(self, statement_text: str) -> ExtractionResult:
        pred = self._module(statement_text=statement_text)
        return ExtractionResult(
            entities=list(pred.entities),
            direction=str(pred.direction),
            magnitude=str(pred.magnitude),
            confidence=float(pred.confidence),
            evidence_quote=str(pred.evidence_quote),
        )


def build_extractor(lm: dspy.LM | None = None) -> _ExtractorAdapter:
    settings = get_settings()
    module = Extract()
    module.set_lm(lm or make_lm(settings.extract_model))
    return _ExtractorAdapter(module, settings.extract_model)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_extract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/llm/extract.py tests/llm/test_extract.py
git commit -m "feat: DSPy Extract module (ChainOfThought) + adapter"
```

---

### Task 8: Stages (detect/extract `process`, guard at the boundary)

**Files:**
- Create: `src/bellwether/worker.py`
- Test: `tests/test_worker.py` (stage tests; the file is extended in Task 9)

**Interfaces:**
- Consumes: `Session`, `Statement`, `Detection`, `Extraction`, `is_verbatim` (Task 3), `Detector`/`Extractor` contracts (Task 4).
- Produces:
  - `Stage` — dataclass: `name: str`, `claim_from: str`, `claim_to: str`, `process: Callable[[Session, Statement], None]`.
  - `make_detect_stage(detector: Detector, threshold: float) -> Stage` — `process` calls `detector.detect`, writes a `Detection` row (`model=detector.model`, `version="baseline"`), sets status `detected` if `is_relevant and score >= threshold` else `irrelevant`, clears `claimed_at`, commits.
  - `make_extract_stage(extractor: Extractor) -> Stage` — `process` calls `extractor.extract`; on any exception OR a non-verbatim `evidence_quote`, sets `extract_failed` and writes NO extraction row; otherwise writes an `Extraction` row and sets `extracted`; clears `claimed_at`, commits.

> Stage `process` operates on a statement the caller has already claimed (status = the in-flight marker). Stage tests use the `db_session` fixture and inject plain-Python stub detectors/extractors — no DSPy, no network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
from datetime import datetime, timezone
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.llm.contracts import DetectionResult, ExtractionResult
from bellwether.worker import make_detect_stage, make_extract_stage


class StubDetector:
    model = "stub/detect"
    def __init__(self, result): self._result = result
    def detect(self, statement_text): return self._result


class StubExtractor:
    model = "stub/extract"
    def __init__(self, result=None, exc=None): self._result, self._exc = result, exc
    def extract(self, statement_text):
        if self._exc is not None:
            raise self._exc
        return self._result


def _statement(db_session, text, status):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    src = Source(figure_id=f.id, connector_type="rss", config={},
                 provenance="primary", origin="manual", owner_id=None)
    db_session.add(src); db_session.flush()
    st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text=text,
                   url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status=status)
    db_session.add(st); db_session.flush()
    return st


def test_detect_stage_marks_detected_above_threshold(db_session):
    st = _statement(db_session, "rates will rise", status="detecting")
    stage = make_detect_stage(StubDetector(DetectionResult(True, 0.9)), threshold=0.5)
    stage.process(db_session, st)
    assert st.status == "detected" and st.claimed_at is None
    d = db_session.execute(select(Detection).where(Detection.statement_id == st.id)).scalar_one()
    assert d.is_relevant is True and d.score == 0.9 and d.model == "stub/detect"


def test_detect_stage_marks_irrelevant_below_threshold(db_session):
    st = _statement(db_session, "nice weather today", status="detecting")
    stage = make_detect_stage(StubDetector(DetectionResult(True, 0.2)), threshold=0.5)
    stage.process(db_session, st)
    assert st.status == "irrelevant"


def test_extract_stage_writes_row_on_verbatim_quote(db_session):
    st = _statement(db_session, "Tesla will grow next quarter.", status="extracting")
    stage = make_extract_stage(StubExtractor(
        ExtractionResult(["TSLA"], "up", "moderate", 0.8, "Tesla will grow")))
    stage.process(db_session, st)
    assert st.status == "extracted"
    e = db_session.execute(select(Extraction).where(Extraction.statement_id == st.id)).scalar_one()
    assert e.entities == ["TSLA"] and e.evidence_quote == "Tesla will grow"


def test_extract_stage_fails_on_non_verbatim_quote(db_session):
    st = _statement(db_session, "Tesla will grow next quarter.", status="extracting")
    stage = make_extract_stage(StubExtractor(
        ExtractionResult(["TSLA"], "up", "moderate", 0.8, "Tesla will SHRINK")))  # fabricated
    stage.process(db_session, st)
    assert st.status == "extract_failed"
    n = db_session.execute(select(func.count()).select_from(Extraction)
                           .where(Extraction.statement_id == st.id)).scalar_one()
    assert n == 0  # no row written for a non-verbatim quote


def test_extract_stage_fails_on_extractor_error(db_session):
    st = _statement(db_session, "anything", status="extracting")
    stage = make_extract_stage(StubExtractor(exc=ValueError("boom")))
    stage.process(db_session, st)
    assert st.status == "extract_failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.worker`.

- [ ] **Step 3: Write the stages**

```python
# src/bellwether/worker.py
from dataclasses import dataclass
from typing import Callable
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.llm.contracts import Detector, Extractor
from bellwether.llm.guard import is_verbatim


@dataclass
class Stage:
    name: str
    claim_from: str
    claim_to: str
    process: Callable[[Session, Statement], None]


def make_detect_stage(detector: Detector, threshold: float) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        result = detector.detect(statement.text)
        session.add(Detection(
            statement_id=statement.id,
            is_relevant=result.is_relevant,
            score=result.score,
            model=detector.model,
            version="baseline",
        ))
        statement.status = "detected" if (result.is_relevant and result.score >= threshold) else "irrelevant"
        statement.claimed_at = None
        session.commit()

    return Stage(name="detect", claim_from="new", claim_to="detecting", process=process)


def make_extract_stage(extractor: Extractor) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        try:
            result = extractor.extract(statement.text)
        except Exception:
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        # Verbatim-substring guard at the stage boundary — outside the module, so no
        # extractor implementation can ever land a fabricated quote.
        if not is_verbatim(result.evidence_quote, statement.text):
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        session.add(Extraction(
            statement_id=statement.id,
            entities=result.entities,
            direction=result.direction,
            magnitude=result.magnitude,
            confidence=result.confidence,
            evidence_quote=result.evidence_quote,
            model=extractor.model,
            version="baseline",
        ))
        statement.status = "extracted"
        statement.claimed_at = None
        session.commit()

    return Stage(name="extract", claim_from="detected", claim_to="extracting", process=process)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_worker.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker.py
git commit -m "feat: detect/extract stages with guard enforced at the stage boundary"
```

---

### Task 9: Worker loop + CLI + end-to-end pipeline

**Files:**
- Modify: `src/bellwether/worker.py` (add `run_worker`, `_build_stage`, `main`, `__main__`)
- Test: `tests/test_worker.py` (add loop + e2e tests)

**Interfaces:**
- Consumes: `Stage` + `make_*_stage` (Task 8), `claim_one` / `reclaim_stale` (Task 5), `SessionLocal` (`bellwether.db`), `build_detector` / `build_extractor` (Tasks 6/7), `get_settings`.
- Produces:
  - `run_worker(stage, *, session_factory=SessionLocal, poll_interval=None, once=False, stop_event=None) -> int` — reclaims stale in-flight rows on startup, then loops `claim_one → stage.process`; on empty queue, returns (if `once`) or sleeps `poll_interval` (interruptible via `stop_event`). A `stage.process` exception is logged and rolled back (the row stays in-flight for later reclaim); the loop continues. Returns the count processed.
  - `main(argv=None)` — argparse CLI: positional `stage` (`detect`/`extract`), `--once`; wires `SIGINT`/`SIGTERM` to a stop event; builds the stage from settings; runs the worker.
  - `python -m bellwether.worker detect|extract [--once]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_worker.py`:
```python
import pytest
from sqlalchemy import delete
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.worker import make_detect_stage, make_extract_stage, run_worker
from bellwether.llm.contracts import DetectionResult, ExtractionResult


def _clear_real():
    with SessionLocal() as s:
        for m in (Extraction, Detection, Statement, Source, Figure, User):
            s.execute(delete(m))
        s.commit()


@pytest.fixture
def clean_db():
    _clear_real()
    yield
    _clear_real()


def _seed_new(text):
    with SessionLocal() as s:
        f = Figure(name="F", type="individual", aliases=[], owner_id=None)
        s.add(f); s.flush()
        src = Source(figure_id=f.id, connector_type="rss", config={},
                     provenance="primary", origin="manual", owner_id=None)
        s.add(src); s.flush()
        st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text=text,
                       url=None, provenance="primary",
                       published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="new")
        s.add(st); s.flush()
        s.commit()
        return st.id


def test_run_worker_once_drains_detect_queue(clean_db):
    _seed_new("rates will rise")
    stage = make_detect_stage(StubDetector(DetectionResult(True, 0.9)), threshold=0.5)
    processed = run_worker(stage, once=True)
    assert processed == 1
    with SessionLocal() as s:
        st = s.execute(select(Statement)).scalar_one()
        assert st.status == "detected"


def test_end_to_end_detect_then_extract(clean_db):
    sid = _seed_new("Tesla will grow next quarter.")
    detect = make_detect_stage(StubDetector(DetectionResult(True, 0.9)), threshold=0.5)
    extract = make_extract_stage(StubExtractor(
        ExtractionResult(["TSLA"], "up", "moderate", 0.8, "Tesla will grow")))
    assert run_worker(detect, once=True) == 1
    assert run_worker(extract, once=True) == 1
    with SessionLocal() as s:
        st = s.get(Statement, sid)
        assert st.status == "extracted"
        assert s.execute(select(func.count()).select_from(Detection)).scalar_one() == 1
        assert s.execute(select(func.count()).select_from(Extraction)).scalar_one() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k "run_worker or end_to_end" -v`
Expected: FAIL with `ImportError: cannot import name 'run_worker'`.

- [ ] **Step 3: Add the worker loop + CLI**

Append to `src/bellwether/worker.py` (add the new imports at the top of the file):
```python
import argparse
import logging
import signal
import threading
from bellwether.db import SessionLocal
from bellwether.config import get_settings
from bellwether.queue import claim_one, reclaim_stale
from bellwether.llm.detect import build_detector
from bellwether.llm.extract import build_extractor

logger = logging.getLogger(__name__)


def run_worker(stage: Stage, *, session_factory=SessionLocal, poll_interval=None,
               once: bool = False, stop_event: "threading.Event | None" = None) -> int:
    settings = get_settings()
    if poll_interval is None:
        poll_interval = settings.worker_poll_interval_seconds

    # Startup crash-recovery: return rows stuck in this stage's in-flight marker.
    with session_factory() as session:
        reclaim_stale(session, stage.claim_to, stage.claim_from,
                      settings.worker_stale_reclaim_seconds)

    processed = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            statement = claim_one(session, stage.claim_from, stage.claim_to)
            if statement is not None:
                try:
                    stage.process(session, statement)
                    processed += 1
                except Exception:
                    session.rollback()  # claim already committed; row reclaimed later
                    logger.exception("stage %s failed for statement id=%s",
                                     stage.name, statement.id)
        if statement is None:
            if once:
                break
            if stop_event is not None:
                if stop_event.wait(poll_interval):
                    break
            else:
                import time
                time.sleep(poll_interval)
    return processed


def _build_stage(name: str) -> Stage:
    settings = get_settings()
    if name == "detect":
        return make_detect_stage(build_detector(), settings.relevance_threshold)
    return make_extract_stage(build_extractor())


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="bellwether.worker")
    parser.add_argument("stage", choices=["detect", "extract"])
    parser.add_argument("--once", action="store_true",
                        help="drain the queue once and exit (default: run as a daemon)")
    args = parser.parse_args(argv)

    stop_event = threading.Event()

    def _handle(signum, frame):
        logger.info("received signal %s — finishing in-flight item then exiting", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    stage = _build_stage(args.stage)
    count = run_worker(stage, once=args.once, stop_event=stop_event)
    logger.info("worker %s processed %d statement(s)", args.stage, count)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_worker.py -k "run_worker or end_to_end" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (Plan 1 + Plan 2 + Plan 3), pristine output.

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/worker.py tests/test_worker.py
git commit -m "feat: worker loop + CLI (detect/extract, --once, graceful shutdown) + e2e"
```

---

## Self-Review

**Spec coverage (Plan 3's slice):**
- Detect stage (cheap model, gates Extract) — Tasks 6, 8 ✓ (`Predict`, threshold at stage boundary).
- Extract stage (structured signal, optimized model) — Tasks 7, 8 ✓ (`ChainOfThought`, typed signature).
- Verbatim-substring guard, enforced outside the module — Tasks 3, 8 ✓ (`is_verbatim` applied in `make_extract_stage.process`).
- Generic `FOR UPDATE SKIP LOCKED` queue harness — Task 5 ✓ (`claim_one`, `reclaim_stale`).
- Worker daemon (long-running poll loop, backoff, graceful shutdown) + one-shot CLI — Task 9 ✓ (`run_worker` + `main`, `--once`).
- `detections` / `extractions` tables (shared corpus, no `owner_id`) + `statements.claimed_at` + status vocabulary — Tasks 1, 8, 9 ✓.
- Provider-agnostic config (model strings in `Settings`, credential in env) — Tasks 2, 6 ✓.
- Frozen `Detector`/`Extractor` contracts + `build_*()` factories (swappable-paradigm seam) — Tasks 4, 6, 7 ✓.
- Tests on real Postgres, no live network (DummyLM for modules; stubs for stages/worker/e2e) — Tasks 5–9 ✓.
- **Deferred with intent:** optimization/golden/champion + eval tables (Plan 5); Resolve + Measure (Plan 4); a scheduler that *fills* the ingest queue on a cadence (later). No task implements these — correct.

**Placeholder scan:** every code step shows complete code; every command has expected output. The only calibration notes (Tasks 6/7) concern the installed `DummyLM` answer-literal shape — the code under test (the Prediction→dataclass mapping) is fully specified; the note is an execution-time API check, not missing logic.

**Type consistency:** `DetectionResult(is_relevant, score)` and `ExtractionResult(entities, direction, magnitude, confidence, evidence_quote)` are defined in Task 4 and consumed identically in Tasks 6–9 and the stubs. `Detector`/`Extractor` carry `.model` + `detect`/`extract`, matched by the adapters (Tasks 6/7) and the stubs (Task 8). `Stage(name, claim_from, claim_to, process)` is defined in Task 8 and consumed by `run_worker` in Task 9. `claim_one(session, from_status, to_status)` / `reclaim_stale(session, in_status, to_status, older_than_seconds)` signatures (Task 5) match their call sites in Task 9. Status strings (`new`/`detecting`/`detected`/`irrelevant`/`extracting`/`extracted`/`extract_failed`) are used consistently across the stages, the queue calls, and the tests. Model/`version` columns (Task 1) match the stage writes (Task 8).
