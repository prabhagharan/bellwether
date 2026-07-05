# bellwether Plan 5 — Evaluation & Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Track-A optimization flywheel — golden labels via a review-and-correct API, held-out evaluation, GEPA optimize + champion/challenger over a versioned program store, and the champion-loading seam into Plan 3's `build_*()` factories — plus the firewall keeping market impact out of accuracy scoring.

**Architecture:** Builds on Plan 3 (DSPy Detect/Extract modules + `build_*()` factories + the `version` column on `detections`/`extractions`). Adds four shared-corpus tables (`relevance_labels`, `extraction_labels`, `eval_runs`, `dspy_programs`); pure Track-A metrics (with GEPA feedback); a review-and-correct API that writes golden labels with a train/held-out split; `dspy.GEPA`-based optimize + champion/challenger promotion (promote iff strictly better on the frozen held-out set) with instant rollback; and modifications so `build_detector()/build_extractor()` load the champion program and stamp its `version`. The firewall: Track-A scoring depends only on labels + model output and is invariant to market data, enforced by test.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, Alembic, Postgres, FastAPI, DSPy 3.2.1 (`GEPA`, `Evaluate`, `Example`, `dump_state`/`load_state` — all verified present), pytest. Design spec: `docs/superpowers/specs/2026-07-05-bellwether-05-evaluation-optimization-design.md`.

## Global Constraints

- Python **3.11+**; SQLAlchemy 2.0 (`Mapped`/`mapped_column`); JSONB via `sqlalchemy.dialects.postgresql`.
- **Shared corpus:** `relevance_labels`, `extraction_labels`, `eval_runs`, `dspy_programs` carry **NO `owner_id`**. Review endpoints are authenticated; the review *queue* is owner-scoped by figure ownership.
- **The firewall:** Track-A code (`bellwether.eval.*`) depends only on `Statement`, labels, the DSPy modules, and `is_verbatim` — it **never imports `Impact`/`Resolution`**. A Track-A score is **invariant to market data**. Tested.
- **Honest held-out:** GEPA sees **only the train split**; the **held-out split is frozen** and used solely for champion/challenger. `split_for(statement_id) = "holdout" if statement_id % holdout_modulus == 0 else "train"` (default modulus 5). A statement is wholly train or wholly held-out.
- **Anti-fabrication preserved:** a human-corrected gold `evidence_quote` must be a verbatim substring (`is_verbatim`) → `422` otherwise.
- **DSPy-only, provider-agnostic:** GEPA reflection model is config-selected (`reflection_model`), credential from env.
- **Instant rollback:** programs are stored artifacts; champion is a boolean flag — promotion/rollback is a DB flip.
- **Champion/challenger:** promote iff `challenger_holdout > champion_holdout` (strictly).
- **Optimizer:** `dspy.GEPA(metric=<feedback metric>, auto=settings.gepa_auto, reflection_lm=make_lm(settings.reflection_model))`; the task models stay Haiku (Detect) / Sonnet 5 (Extract).
- **Tests: real Postgres, no live network.** Metrics are pure; eval/review use stub predictors + real Postgres; the **optimize orchestration is tested with a fake compile/eval** (real GEPA `compile` is LLM-intensive and run manually/live, like the market adapter in Plan 4). A yfinance transitive import adds ~15-20s one-time to a full-suite run — normal.
- **ENVIRONMENT:** run via `.venv/bin/python -m …` / `.venv/bin/alembic …`; Postgres via `docker compose up -d`; `.env` exists.

## File Structure

```
src/bellwether/
├── models/            # relevance_label.py, extraction_label.py, dspy_program.py, eval_run.py (+ register)
├── labels.py          # split_for(); upsert_relevance_label(); upsert_extraction_label()
├── programs.py        # next_version, save_program, load_champion, set_champion, list_programs
├── eval/
│   ├── __init__.py
│   ├── metrics.py     # GoldExtraction, score_detection, score_extraction (pure, + feedback)
│   ├── evaluate.py    # EvalResult, evaluate_detect, evaluate_extract (Track-A; firewall boundary)
│   ├── gepa_metric.py # detect_metric / extract_metric (DSPy-Prediction wrappers)
│   └── optimize.py    # build_trainset, promote_if_better, optimize, gepa_compile, evaluate_holdout, run_optimize
├── trackb/report.py   # avg_pct_move_by_figure (minimal, firewall-separated)
├── config.py          # (modify) reflection_model, gepa_auto, holdout_modulus
├── llm/detect.py, llm/extract.py, llm/contracts.py  # (modify) champion-load + .version
├── worker.py          # (modify) stages stamp version=detector.version; _build_stage loads champion
└── api/
    ├── schemas.py     # (modify) review + program + eval schemas
    ├── review.py, programs.py, optimize_api.py   # new routers
    └── app.py         # (modify) include the three routers
migrations/versions/   # ONE migration: relevance_labels, extraction_labels, dspy_programs, eval_runs
tests/                 # per task
```

---

### Task 1: Models + migration (labels, programs, eval_runs)

**Files:**
- Create: `src/bellwether/models/relevance_label.py`, `extraction_label.py`, `dspy_program.py`, `eval_run.py`
- Modify: `src/bellwether/models/__init__.py`
- Create: the generated migration
- Test: `tests/test_models_eval.py`

**Interfaces:**
- Produces (all shared corpus, no `owner_id`):
  - `RelevanceLabel` (`relevance_labels`): `id` PK, `statement_id` FK→statements.id (CASCADE, **unique**, indexed), `is_relevant` bool, `source` str default `"review"`, `split` str, `created_at`.
  - `ExtractionLabel` (`extraction_labels`): `id` PK, `statement_id` FK→statements.id (CASCADE, **unique**, indexed), `entities` JSONB (list, default `[]`), `direction` str, `magnitude` str, `evidence_quote` Text, `source` str default `"review"`, `split` str, `created_at`.
  - `DspyProgram` (`dspy_programs`): `id` PK, `module` str (indexed), `version` int, `artifact` JSONB (dict, default `dict`), `holdout_score` float|null, `is_champion` bool default `False`, `created_at`. **Unique `(module, version)`.**
  - `EvalRun` (`eval_runs`): `id` PK, `module` str, `dspy_program_id` FK→dspy_programs.id (nullable), `split` str, `metric` str, `score` float, `n` int, `created_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_eval.py
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun


def test_label_columns_and_unique():
    rc = set(RelevanceLabel.__table__.columns.keys())
    assert {"id", "statement_id", "is_relevant", "source", "split", "created_at"} <= rc
    assert "owner_id" not in rc
    assert RelevanceLabel.__table__.columns["statement_id"].unique is True
    ec = set(ExtractionLabel.__table__.columns.keys())
    assert {"id", "statement_id", "entities", "direction", "magnitude",
            "evidence_quote", "source", "split", "created_at"} <= ec
    assert ExtractionLabel.__table__.columns["statement_id"].unique is True


def test_program_and_evalrun_columns():
    pc = set(DspyProgram.__table__.columns.keys())
    assert {"id", "module", "version", "artifact", "holdout_score", "is_champion", "created_at"} <= pc
    assert "owner_id" not in pc
    uniques = [c for c in DspyProgram.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"module", "version"} for u in uniques)
    er = set(EvalRun.__table__.columns.keys())
    assert {"id", "module", "dspy_program_id", "split", "metric", "score", "n", "created_at"} <= er
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.models.relevance_label`.

- [ ] **Step 3: Write the label models**

```python
# src/bellwether/models/relevance_label.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class RelevanceLabel(Base):
    __tablename__ = "relevance_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="review")
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

```python
# src/bellwether/models/extraction_label.py
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class ExtractionLabel(Base):
    __tablename__ = "extraction_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    magnitude: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="review")
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Write the program + eval_run models**

```python
# src/bellwether/models/dspy_program.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class DspyProgram(Base):
    __tablename__ = "dspy_programs"
    __table_args__ = (UniqueConstraint("module", "version", name="uq_dspy_programs_module_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    holdout_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_champion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

```python
# src/bellwether/models/eval_run.py
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    dspy_program_id: Mapped[int | None] = mapped_column(
        ForeignKey("dspy_programs.id", ondelete="SET NULL"), nullable=True
    )
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    metric: Mapped[str] = mapped_column(String(30), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: Register the models**

```python
# src/bellwether/models/__init__.py  (append the imports + __all__ entries)
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun
```
Add `"RelevanceLabel", "ExtractionLabel", "DspyProgram", "EvalRun"` to `__all__`.

- [ ] **Step 6: Run the model test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models_eval.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Generate and apply the migration**

Run:
```bash
.venv/bin/alembic revision --autogenerate -m "create relevance_labels, extraction_labels, dspy_programs, eval_runs"
.venv/bin/alembic upgrade head
```
Expected: a new version file; `upgrade head` completes. Open it and confirm it creates all four tables with the CASCADE FKs, the unique `statement_id` on both label tables, the `uq_dspy_programs_module_version` unique, the `dspy_programs.module` index, and the `SET NULL` FK on `eval_runs.dspy_program_id`. No hand-editing beyond autogenerate.

- [ ] **Step 8: Commit**

```bash
git add src/bellwether/models tests/test_models_eval.py migrations
git commit -m "feat: relevance/extraction labels + dspy_programs + eval_runs models + migration"
```

---

### Task 2: Config — reflection/GEPA/split settings

**Files:**
- Modify: `src/bellwether/config.py`, `.env.example`
- Test: `tests/test_config_eval.py`

**Interfaces:**
- Produces on `Settings`: `reflection_model: str = "anthropic/claude-sonnet-5"`, `gepa_auto: str = "light"`, `holdout_modulus: int = 5`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_eval.py
from bellwether.config import Settings


def test_eval_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.reflection_model == "anthropic/claude-sonnet-5"
    assert s.gepa_auto == "light"
    assert s.holdout_modulus == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_eval.py -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the settings**

In `src/bellwether/config.py`, add inside `class Settings` after the Plan-4 settings:
```python
    reflection_model: str = "anthropic/claude-sonnet-5"
    gepa_auto: str = "light"
    holdout_modulus: int = 5
```

- [ ] **Step 4: Document in `.env.example`**

Append:
```bash
# --- Evaluation & optimization (Plan 5) ---
# REFLECTION_MODEL=anthropic/claude-sonnet-5   # GEPA reflection LM (proposes better prompts)
# GEPA_AUTO=light                              # GEPA budget preset: light|medium|heavy
# HOLDOUT_MODULUS=5                            # statement_id %% N == 0 -> held-out (~1/N)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_eval.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/config.py .env.example tests/test_config_eval.py
git commit -m "feat: reflection/GEPA/holdout settings"
```

---

### Task 3: Track-A metrics (pure, with feedback)

**Files:**
- Create: `src/bellwether/eval/__init__.py` (empty), `src/bellwether/eval/metrics.py`
- Test: `tests/eval/__init__.py` (empty), `tests/eval/test_metrics.py`

**Interfaces:**
- Consumes: `is_verbatim` (`bellwether.llm.guard`), `ExtractionResult` (`bellwether.llm.contracts`, for the `pred` shape).
- Produces:
  - `GoldExtraction(entities: list[str], direction: str, magnitude: str, evidence_quote: str)` — frozen dataclass.
  - `score_detection(pred_is_relevant: bool, gold_is_relevant: bool) -> tuple[float, str]` — `(1.0,"ok")` iff match else `(0.0, feedback)`.
  - `score_extraction(pred, gold: GoldExtraction, statement_text: str) -> tuple[float, str]` — mean of four parts (direction exact, magnitude exact, evidence verbatim-valid, entity set-F1) + a feedback string. `pred` is any object with `.direction`/`.magnitude`/`.entities`/`.evidence_quote`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_metrics.py
from dataclasses import dataclass
from bellwether.eval.metrics import GoldExtraction, score_detection, score_extraction

SRC = "Tesla will grow production and deliveries this quarter."


@dataclass
class Pred:
    entities: list
    direction: str
    magnitude: str
    evidence_quote: str


def test_score_detection():
    assert score_detection(True, True)[0] == 1.0
    s, fb = score_detection(True, False)
    assert s == 0.0 and "relevance" in fb


def test_extraction_perfect():
    gold = GoldExtraction(["TSLA"], "up", "moderate", "Tesla will grow")
    pred = Pred(["tsla"], "up", "moderate", "Tesla will grow")
    s, fb = score_extraction(pred, gold, SRC)
    assert s == 1.0 and fb == "ok"


def test_extraction_partial_and_feedback():
    gold = GoldExtraction(["TSLA"], "up", "moderate", "Tesla will grow")
    # wrong direction, non-verbatim quote, entities miss -> only magnitude(1) + entityF1(0) ... compute:
    pred = Pred(["FORD"], "down", "moderate", "Tesla will SHRINK")
    s, fb = score_extraction(pred, gold, SRC)
    # direction 0, magnitude 1, evidence 0 (not a substring), entityF1 0 -> mean = 0.25
    assert abs(s - 0.25) < 1e-9
    assert "direction" in fb and "evidence" in fb and "entities" in fb


def test_entity_f1_partial():
    gold = GoldExtraction(["TSLA", "GM"], "up", "small", "Tesla will grow")
    pred = Pred(["TSLA"], "up", "small", "Tesla will grow")
    s, fb = score_extraction(pred, gold, SRC)
    # d1 m1 e1 ; entityF1 = 2*(1/1)*(1/2)/(1/1+1/2)=0.6667 -> mean=(1+1+1+0.6667)/4=0.9167
    assert abs(s - 0.91666666) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/eval/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.eval.metrics`.

- [ ] **Step 3: Write the metrics**

Create empty `src/bellwether/eval/__init__.py` and empty `tests/eval/__init__.py`, then:
```python
# src/bellwether/eval/metrics.py
from dataclasses import dataclass
from bellwether.llm.guard import is_verbatim


@dataclass(frozen=True)
class GoldExtraction:
    entities: list[str]
    direction: str
    magnitude: str
    evidence_quote: str


def score_detection(pred_is_relevant: bool, gold_is_relevant: bool) -> tuple[float, str]:
    if bool(pred_is_relevant) == bool(gold_is_relevant):
        return 1.0, "ok"
    return 0.0, f"relevance wrong: pred={bool(pred_is_relevant)} gold={bool(gold_is_relevant)}"


def _entity_f1(pred: list[str], gold: list[str]) -> float:
    p = {e.strip().lower() for e in pred if e and e.strip()}
    g = {e.strip().lower() for e in gold if e and e.strip()}
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    inter = len(p & g)
    if inter == 0:
        return 0.0
    precision = inter / len(p)
    recall = inter / len(g)
    return 2 * precision * recall / (precision + recall)


def score_extraction(pred, gold: GoldExtraction, statement_text: str) -> tuple[float, str]:
    d = 1.0 if str(pred.direction) == str(gold.direction) else 0.0
    m = 1.0 if str(pred.magnitude) == str(gold.magnitude) else 0.0
    e = 1.0 if is_verbatim(pred.evidence_quote, statement_text) else 0.0
    ef = _entity_f1(list(pred.entities), list(gold.entities))
    score = (d + m + e + ef) / 4.0
    parts = []
    if d == 0.0:
        parts.append(f"direction wrong (pred {pred.direction}, gold {gold.direction})")
    if m == 0.0:
        parts.append(f"magnitude wrong (pred {pred.magnitude}, gold {gold.magnitude})")
    if e == 0.0:
        parts.append("evidence_quote not a verbatim substring")
    if ef < 1.0:
        parts.append(f"entities off (F1={ef:.2f})")
    return score, ("ok" if not parts else "; ".join(parts))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/eval/test_metrics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/eval/__init__.py src/bellwether/eval/metrics.py tests/eval
git commit -m "feat: pure Track-A metrics (detect accuracy + 4-part extract score + feedback)"
```

---

### Task 4: Split + label helpers

**Files:**
- Create: `src/bellwether/labels.py`
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: `RelevanceLabel`, `ExtractionLabel` (Task 1), `is_verbatim`, `get_settings`.
- Produces:
  - `split_for(statement_id: int) -> str` — `"holdout"` if `statement_id % settings.holdout_modulus == 0` else `"train"`.
  - `upsert_relevance_label(session, statement_id, is_relevant, source="review") -> RelevanceLabel` — insert or update; split assigned on insert.
  - `upsert_extraction_label(session, statement_id, entities, direction, magnitude, evidence_quote, statement_text, source="review") -> ExtractionLabel` — raises `ValueError` if `evidence_quote` is not a verbatim substring of `statement_text`; insert or update.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_labels.py
from datetime import datetime, timezone
import pytest
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.labels import split_for, upsert_relevance_label, upsert_extraction_label
from sqlalchemy import select


def _statement(db_session, text="Tesla will grow production."):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text=text, url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted")
    db_session.add(st); db_session.flush()
    return st


def test_split_for_deterministic():
    assert split_for(5) == "holdout" and split_for(10) == "holdout"
    assert split_for(4) == "train" and split_for(7) == "train"


def test_upsert_relevance_label_insert_then_update(db_session):
    st = _statement(db_session)
    lab = upsert_relevance_label(db_session, st.id, True)
    assert lab.is_relevant is True and lab.split in ("train", "holdout")
    lab2 = upsert_relevance_label(db_session, st.id, False)
    assert lab2.id == lab.id and lab2.is_relevant is False  # updated, not duplicated
    assert db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st.id)).scalars().all().__len__() == 1


def test_upsert_extraction_label_verbatim_guard(db_session):
    st = _statement(db_session, "Tesla will grow production this quarter.")
    lab = upsert_extraction_label(db_session, st.id, ["TSLA"], "up", "small", "Tesla will grow", st.text)
    assert lab.direction == "up"
    with pytest.raises(ValueError):
        upsert_extraction_label(db_session, st.id, ["TSLA"], "up", "small", "Tesla will SHRINK", st.text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.labels`.

- [ ] **Step 3: Write the label helpers**

```python
# src/bellwether/labels.py
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.llm.guard import is_verbatim
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel


def split_for(statement_id: int) -> str:
    return "holdout" if statement_id % get_settings().holdout_modulus == 0 else "train"


def upsert_relevance_label(session: Session, statement_id: int, is_relevant: bool,
                           source: str = "review") -> RelevanceLabel:
    row = session.execute(
        select(RelevanceLabel).where(RelevanceLabel.statement_id == statement_id)
    ).scalar_one_or_none()
    if row is None:
        row = RelevanceLabel(statement_id=statement_id, is_relevant=is_relevant,
                             source=source, split=split_for(statement_id))
        session.add(row)
    else:
        row.is_relevant = is_relevant
        row.source = source
    session.flush()
    return row


def upsert_extraction_label(session: Session, statement_id: int, entities: list[str],
                            direction: str, magnitude: str, evidence_quote: str,
                            statement_text: str, source: str = "review") -> ExtractionLabel:
    if not is_verbatim(evidence_quote, statement_text):
        raise ValueError("gold evidence_quote must be a verbatim substring of the statement")
    row = session.execute(
        select(ExtractionLabel).where(ExtractionLabel.statement_id == statement_id)
    ).scalar_one_or_none()
    if row is None:
        row = ExtractionLabel(statement_id=statement_id, entities=entities, direction=direction,
                              magnitude=magnitude, evidence_quote=evidence_quote,
                              source=source, split=split_for(statement_id))
        session.add(row)
    else:
        row.entities = entities
        row.direction = direction
        row.magnitude = magnitude
        row.evidence_quote = evidence_quote
        row.source = source
    session.flush()
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_labels.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/labels.py tests/test_labels.py
git commit -m "feat: golden-label helpers (split_for + verbatim-guarded upserts)"
```

---

### Task 5: Program store (champion save/load/promote)

**Files:**
- Create: `src/bellwether/programs.py`
- Test: `tests/test_programs.py`

**Interfaces:**
- Consumes: `DspyProgram` (Task 1).
- Produces:
  - `next_version(session, module) -> int` — `max(version)+1` for the module (1 if none).
  - `save_program(session, module, version, artifact, holdout_score=None, is_champion=False) -> DspyProgram`.
  - `load_champion(session, module) -> tuple[dict, int] | None` — `(artifact, version)` of the champion, or `None`.
  - `set_champion(session, program_id) -> DspyProgram | None` — demotes any current champion of that module, sets this one; `None` if the id is missing.
  - `list_programs(session, module=None) -> list[DspyProgram]` — newest version first.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_programs.py
from bellwether.programs import next_version, save_program, load_champion, set_champion, list_programs


def test_versioning_and_champion(db_session):
    assert next_version(db_session, "detect") == 1
    p1 = save_program(db_session, "detect", 1, {"a": 1}); db_session.flush()
    assert next_version(db_session, "detect") == 2
    p2 = save_program(db_session, "detect", 2, {"a": 2}); db_session.flush()
    assert load_champion(db_session, "detect") is None  # nothing promoted yet
    set_champion(db_session, p1.id); db_session.flush()
    assert load_champion(db_session, "detect") == ({"a": 1}, 1)
    set_champion(db_session, p2.id); db_session.flush()  # promotes p2, demotes p1
    assert load_champion(db_session, "detect") == ({"a": 2}, 2)
    db_session.refresh(p1)
    assert p1.is_champion is False
    assert [p.version for p in list_programs(db_session, "detect")] == [2, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_programs.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.programs`.

- [ ] **Step 3: Write the program store**

```python
# src/bellwether/programs.py
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from bellwether.models.dspy_program import DspyProgram


def next_version(session: Session, module: str) -> int:
    mx = session.execute(
        select(func.max(DspyProgram.version)).where(DspyProgram.module == module)
    ).scalar()
    return (mx or 0) + 1


def save_program(session: Session, module: str, version: int, artifact: dict,
                 holdout_score: float | None = None, is_champion: bool = False) -> DspyProgram:
    program = DspyProgram(module=module, version=version, artifact=artifact,
                          holdout_score=holdout_score, is_champion=is_champion)
    session.add(program)
    session.flush()
    return program


def load_champion(session: Session, module: str) -> tuple[dict, int] | None:
    program = session.execute(
        select(DspyProgram).where(DspyProgram.module == module, DspyProgram.is_champion.is_(True))
    ).scalar_one_or_none()
    return (program.artifact, program.version) if program is not None else None


def set_champion(session: Session, program_id: int) -> DspyProgram | None:
    program = session.get(DspyProgram, program_id)
    if program is None:
        return None
    session.execute(
        update(DspyProgram).where(DspyProgram.module == program.module).values(is_champion=False)
    )
    program.is_champion = True
    session.flush()
    return program


def list_programs(session: Session, module: str | None = None) -> list[DspyProgram]:
    query = select(DspyProgram)
    if module is not None:
        query = query.where(DspyProgram.module == module)
    return list(session.execute(query.order_by(DspyProgram.module, DspyProgram.version.desc())).scalars())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_programs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/programs.py tests/test_programs.py
git commit -m "feat: dspy_programs store (versioning + champion load/promote)"
```

---

### Task 6: Champion-loading seam (build_* + version stamping)

**Files:**
- Modify: `src/bellwether/llm/contracts.py`, `src/bellwether/llm/detect.py`, `src/bellwether/llm/extract.py`, `src/bellwether/worker.py`
- Test: `tests/llm/test_champion_seam.py`

**Interfaces:**
- Produces:
  - `Detector`/`Extractor` protocols gain a `version: str` attribute.
  - `build_detector(lm=None, program_state=None, version="baseline") -> Detector` — if `program_state` (a `dump_state()` dict) is given, `module.load_state(program_state)`; adapter exposes `.version`.
  - `build_extractor(lm=None, program_state=None, version="baseline") -> Extractor` — same, keeping the `AdapterParseError→ExtractionParseError` translation.
  - `make_detect_stage`/`make_extract_stage` stamp `version=detector.version` / `extractor.version` on the `Detection`/`Extraction` rows.
  - `worker._build_stage` loads the champion (via `load_champion`) for detect/extract and passes `program_state`+`version` to `build_*`.

- [ ] **Step 1: Write the failing test**

```python
# tests/llm/test_champion_seam.py
import dspy
from sqlalchemy import select
from datetime import datetime, timezone
from bellwether.llm.detect import Detect, build_detector
from bellwether.llm.extract import Extract, build_extractor
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.llm.contracts import DetectionResult
from bellwether.worker import make_detect_stage


def test_build_detector_default_version_baseline():
    assert build_detector().version == "baseline"


def test_build_detector_loads_program_and_stamps_version():
    state = Detect().dump_state()  # a valid state dict; no network
    d = build_detector(program_state=state, version="7")
    assert d.version == "7"


def test_build_extractor_default_version_baseline():
    assert build_extractor().version == "baseline"


class _StubDetector:
    model = "stub/detect"
    version = "9"
    def detect(self, text): return DetectionResult(True, 0.9)


def test_detect_stage_stamps_detector_version(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="rates rise", url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="detecting")
    db_session.add(st); db_session.flush()
    make_detect_stage(_StubDetector(), threshold=0.5).process(db_session, st)
    d = db_session.execute(select(Detection).where(Detection.statement_id == st.id)).scalar_one()
    assert d.version == "9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_champion_seam.py -v`
Expected: FAIL (`build_detector()` has no `.version`, or `TypeError` on `program_state`).

- [ ] **Step 3: Add `version` to the contracts**

In `src/bellwether/llm/contracts.py`, add `version: str` to both protocols:
```python
@runtime_checkable
class Detector(Protocol):
    model: str
    version: str
    def detect(self, statement_text: str) -> DetectionResult: ...


@runtime_checkable
class Extractor(Protocol):
    model: str
    version: str
    def extract(self, statement_text: str) -> ExtractionResult: ...
```
(The existing `Resolver` protocol is unchanged.)

- [ ] **Step 4: Update `build_detector`**

Replace `_DetectorAdapter` + `build_detector` in `src/bellwether/llm/detect.py`:
```python
class _DetectorAdapter:
    def __init__(self, module: Detect, model: str, version: str):
        self._module = module
        self.model = model
        self.version = version

    def detect(self, statement_text: str) -> DetectionResult:
        pred = self._module(statement_text=statement_text)
        return DetectionResult(is_relevant=bool(pred.is_relevant), score=float(pred.score))


def build_detector(lm: dspy.LM | None = None, program_state: dict | None = None,
                   version: str = "baseline") -> Detector:
    settings = get_settings()
    module = Detect()
    module.set_lm(lm or make_lm(settings.detect_model))
    if program_state is not None:
        module.load_state(program_state)
    return _DetectorAdapter(module, settings.detect_model, version)
```

- [ ] **Step 5: Update `build_extractor`**

In `src/bellwether/llm/extract.py`, give `_ExtractorAdapter` a `version` (constructor + attribute) and update `build_extractor` the same way (keep the `AdapterParseError→ExtractionParseError` translation in `.extract`):
```python
class _ExtractorAdapter:
    def __init__(self, module: Extract, model: str, version: str):
        self._module = module
        self.model = model
        self.version = version

    def extract(self, statement_text: str) -> ExtractionResult:
        try:
            pred = self._module(statement_text=statement_text)
        except AdapterParseError as exc:
            raise ExtractionParseError(str(exc)) from exc
        return ExtractionResult(
            entities=list(pred.entities), direction=str(pred.direction),
            magnitude=str(pred.magnitude), confidence=float(pred.confidence),
            evidence_quote=str(pred.evidence_quote),
        )


def build_extractor(lm: dspy.LM | None = None, program_state: dict | None = None,
                    version: str = "baseline") -> Extractor:
    settings = get_settings()
    module = Extract()
    module.set_lm(lm or make_lm(settings.extract_model))
    if program_state is not None:
        module.load_state(program_state)
    return _ExtractorAdapter(module, settings.extract_model, version)
```

- [ ] **Step 6: Stamp the version in the stages + load the champion in `_build_stage`**

In `src/bellwether/worker.py`:
- In `make_detect_stage.process`, change the `Detection(... version="baseline")` to `version=detector.version`.
- In `make_extract_stage.process`, change the `Extraction(... version="baseline")` to `version=extractor.version`.
- Add `from bellwether.programs import load_champion` at the top.
- Replace the detect/extract branches of `_build_stage` to load the champion:
```python
def _build_stage(name: str) -> Stage:
    settings = get_settings()
    if name == "detect":
        with SessionLocal() as s:
            champ = load_champion(s, "detect")
        detector = (build_detector(program_state=champ[0], version=str(champ[1]))
                    if champ else build_detector())
        return make_detect_stage(detector, settings.relevance_threshold)
    if name == "extract":
        with SessionLocal() as s:
            champ = load_champion(s, "extract")
        extractor = (build_extractor(program_state=champ[0], version=str(champ[1]))
                     if champ else build_extractor())
        return make_extract_stage(extractor)
    if name == "resolve":
        return make_resolve_stage(build_resolver(), parse_windows(settings.measure_windows))
    return make_measure_stage(build_market_data(), settings.measure_baseline_bars)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/llm/test_champion_seam.py tests/test_worker.py -v`
Expected: PASS (the seam tests plus the existing worker tests — the detect/extract stage tests still pass because the stub detectors/extractors now need a `.version`; **the existing stubs in `tests/test_worker.py` set `model` but not `version`** — if a worker test fails on a missing `.version`, add `version = "baseline"` to that test's `StubDetector`/`StubExtractor` class, a constructor-only change, no asserted behavior altered).

- [ ] **Step 8: Run the full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.
```bash
git add src/bellwether/llm/contracts.py src/bellwether/llm/detect.py src/bellwether/llm/extract.py src/bellwether/worker.py tests/llm/test_champion_seam.py tests/test_worker.py
git commit -m "feat: champion-loading seam (build_* loads program + stamps version)"
```

---

### Task 7: Evaluate (Track A)

**Files:**
- Create: `src/bellwether/eval/evaluate.py`
- Test: `tests/eval/test_evaluate.py`

**Interfaces:**
- Consumes: `Statement`, `RelevanceLabel`, `ExtractionLabel`, `EvalRun` models; `score_detection`/`score_extraction`/`GoldExtraction` (Task 3). **Imports NO `Impact`/`Resolution`** (firewall).
- Produces:
  - `EvalResult(score: float, n: int)` — frozen dataclass.
  - `evaluate_detect(session, detector, split, dspy_program_id=None) -> EvalResult` — runs `detector.detect` over statements with a `relevance_labels` row for `split`, averages `score_detection`, writes an `eval_runs` row (`metric="accuracy"`).
  - `evaluate_extract(session, extractor, split, dspy_program_id=None) -> EvalResult` — same over `extraction_labels` (`metric="extract_avg"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_evaluate.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.eval_run import EvalRun
from bellwether.llm.contracts import DetectionResult, ExtractionResult
from bellwether.eval.evaluate import evaluate_detect, evaluate_extract, EvalResult


def _stmt(db_session, text, sid_status="detected"):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id=text[:8], text=text, url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status=sid_status)
    db_session.add(st); db_session.flush()
    return st


class StubDetector:
    model = "stub"; version = "baseline"
    def detect(self, text): return DetectionResult(True, 0.9)  # always relevant


def test_evaluate_detect_accuracy_and_run_row(db_session):
    a = _stmt(db_session, "rates rise")
    b = _stmt(db_session, "nice weather")
    db_session.add(RelevanceLabel(statement_id=a.id, is_relevant=True, source="review", split="holdout"))
    db_session.add(RelevanceLabel(statement_id=b.id, is_relevant=False, source="review", split="holdout"))
    db_session.flush()
    res = evaluate_detect(db_session, StubDetector(), "holdout")
    assert isinstance(res, EvalResult) and res.n == 2 and abs(res.score - 0.5) < 1e-9  # 1 right, 1 wrong
    run = db_session.execute(select(EvalRun).where(EvalRun.module == "detect")).scalar_one()
    assert run.split == "holdout" and run.metric == "accuracy" and run.n == 2


class StubExtractor:
    model = "stub"; version = "baseline"
    def extract(self, text): return ExtractionResult(["TSLA"], "up", "small", 0.5, "Tesla will grow")


def test_evaluate_extract_score(db_session):
    st = _stmt(db_session, "Tesla will grow next quarter.", "extracted")
    db_session.add(ExtractionLabel(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="small",
                                   evidence_quote="Tesla will grow", source="review", split="holdout"))
    db_session.flush()
    res = evaluate_extract(db_session, StubExtractor(), "holdout")
    assert res.n == 1 and abs(res.score - 1.0) < 1e-9  # perfect match
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/eval/test_evaluate.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.eval.evaluate`.

- [ ] **Step 3: Write the evaluator**

```python
# src/bellwether/eval/evaluate.py
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.eval_run import EvalRun
from bellwether.eval.metrics import score_detection, score_extraction, GoldExtraction

# Firewall: this module imports Statement + labels + metrics only — never Impact/Resolution.


@dataclass(frozen=True)
class EvalResult:
    score: float
    n: int


def evaluate_detect(session: Session, detector, split: str, dspy_program_id: int | None = None) -> EvalResult:
    rows = session.execute(
        select(Statement, RelevanceLabel)
        .join(RelevanceLabel, RelevanceLabel.statement_id == Statement.id)
        .where(RelevanceLabel.split == split)
    ).all()
    if rows:
        total = 0.0
        for st, lab in rows:
            pred = detector.detect(st.text)
            s, _ = score_detection(pred.is_relevant, lab.is_relevant)
            total += s
        result = EvalResult(total / len(rows), len(rows))
    else:
        result = EvalResult(0.0, 0)
    session.add(EvalRun(module="detect", dspy_program_id=dspy_program_id, split=split,
                        metric="accuracy", score=result.score, n=result.n))
    session.flush()
    return result


def evaluate_extract(session: Session, extractor, split: str, dspy_program_id: int | None = None) -> EvalResult:
    rows = session.execute(
        select(Statement, ExtractionLabel)
        .join(ExtractionLabel, ExtractionLabel.statement_id == Statement.id)
        .where(ExtractionLabel.split == split)
    ).all()
    if rows:
        total = 0.0
        for st, lab in rows:
            pred = extractor.extract(st.text)
            gold = GoldExtraction(entities=lab.entities, direction=lab.direction,
                                  magnitude=lab.magnitude, evidence_quote=lab.evidence_quote)
            s, _ = score_extraction(pred, gold, st.text)
            total += s
        result = EvalResult(total / len(rows), len(rows))
    else:
        result = EvalResult(0.0, 0)
    session.add(EvalRun(module="extract", dspy_program_id=dspy_program_id, split=split,
                        metric="extract_avg", score=result.score, n=result.n))
    session.flush()
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/eval/test_evaluate.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/eval/evaluate.py tests/eval/test_evaluate.py
git commit -m "feat: Track-A evaluate (detect accuracy + extract score over held-out labels)"
```

---

### Task 8: GEPA metric adapter + trainset builder

**Files:**
- Create: `src/bellwether/eval/gepa_metric.py`
- Create: `src/bellwether/eval/optimize.py` (with `build_trainset` only in this task; the rest is Task 9)
- Test: `tests/eval/test_gepa_and_trainset.py`

**Interfaces:**
- Consumes: `score_detection`/`score_extraction`/`GoldExtraction` (Task 3); `Statement`, `RelevanceLabel`, `ExtractionLabel`; `dspy`.
- Produces:
  - `gepa_metric.detect_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> dspy.Prediction(score, feedback)`.
  - `gepa_metric.extract_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> dspy.Prediction(score, feedback)` — reads `gold.statement_text` + gold fields.
  - `optimize.build_trainset(session, module, split="train") -> list[dspy.Example]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_gepa_and_trainset.py
from datetime import datetime, timezone
import dspy
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.eval.gepa_metric import detect_metric, extract_metric
from bellwether.eval.optimize import build_trainset


def test_extract_metric_returns_score_and_feedback():
    gold = dspy.Example(statement_text="Tesla will grow.", entities=["TSLA"],
                        direction="up", magnitude="small", evidence_quote="Tesla will grow")
    pred = dspy.Prediction(entities=["TSLA"], direction="up", magnitude="small", evidence_quote="Tesla will grow")
    out = extract_metric(gold, pred)
    assert abs(out.score - 1.0) < 1e-9 and out.feedback == "ok"


def test_detect_metric():
    gold = dspy.Example(statement_text="x", is_relevant=True)
    out = detect_metric(gold, dspy.Prediction(is_relevant=False))
    assert out.score == 0.0 and "relevance" in out.feedback


def test_build_trainset_detect(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="rates rise", url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="detected")
    db_session.add(st); db_session.flush()
    db_session.add(RelevanceLabel(statement_id=st.id, is_relevant=True, source="review", split="train"))
    db_session.flush()
    ts = build_trainset(db_session, "detect", "train")
    assert len(ts) == 1 and ts[0].statement_text == "rates rise" and ts[0].is_relevant is True
    assert ts[0].inputs().keys() == {"statement_text"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/eval/test_gepa_and_trainset.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.eval.gepa_metric`.

- [ ] **Step 3: Write the GEPA metric adapter**

```python
# src/bellwether/eval/gepa_metric.py
import dspy
from bellwether.eval.metrics import score_detection, score_extraction, GoldExtraction


def detect_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    score, feedback = score_detection(pred.is_relevant, gold.is_relevant)
    return dspy.Prediction(score=score, feedback=feedback)


def extract_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    g = GoldExtraction(entities=list(gold.entities), direction=gold.direction,
                       magnitude=gold.magnitude, evidence_quote=gold.evidence_quote)
    score, feedback = score_extraction(pred, g, gold.statement_text)
    return dspy.Prediction(score=score, feedback=feedback)
```

- [ ] **Step 4: Write `build_trainset`**

```python
# src/bellwether/eval/optimize.py
import dspy
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel


def build_trainset(session: Session, module: str, split: str = "train") -> list[dspy.Example]:
    if module == "detect":
        rows = session.execute(
            select(Statement, RelevanceLabel)
            .join(RelevanceLabel, RelevanceLabel.statement_id == Statement.id)
            .where(RelevanceLabel.split == split)
        ).all()
        return [dspy.Example(statement_text=st.text, is_relevant=lab.is_relevant)
                .with_inputs("statement_text") for st, lab in rows]
    rows = session.execute(
        select(Statement, ExtractionLabel)
        .join(ExtractionLabel, ExtractionLabel.statement_id == Statement.id)
        .where(ExtractionLabel.split == split)
    ).all()
    return [dspy.Example(statement_text=st.text, entities=lab.entities, direction=lab.direction,
                         magnitude=lab.magnitude, evidence_quote=lab.evidence_quote)
            .with_inputs("statement_text") for st, lab in rows]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/eval/test_gepa_and_trainset.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/bellwether/eval/gepa_metric.py src/bellwether/eval/optimize.py tests/eval/test_gepa_and_trainset.py
git commit -m "feat: GEPA feedback-metric adapters + DSPy trainset builder"
```

---

### Task 9: Optimize + champion/challenger

**Files:**
- Modify: `src/bellwether/eval/optimize.py` (add `promote_if_better`, `optimize`, `gepa_compile`, `evaluate_holdout`, `run_optimize`)
- Test: `tests/eval/test_optimize.py`

**Interfaces:**
- Consumes: `build_trainset` (Task 8), `load_champion`/`save_program`/`next_version`/`set_champion` (Task 5), `evaluate_detect`/`evaluate_extract` + `build_detector`/`build_extractor`, `dspy.GEPA`, `make_lm`, `get_settings`, `Detect`/`Extract`, `detect_metric`/`extract_metric`.
- Produces:
  - `OptimizeResult(module, version, challenger_holdout, champion_holdout, promoted)` — frozen dataclass.
  - `promote_if_better(challenger_holdout, champion_holdout) -> bool` — strict `>`.
  - `optimize(session, module, *, compile_fn, evaluate_holdout_fn) -> OptimizeResult` — orchestration (train → compile → save program → holdout-eval challenger → compare to champion holdout → promote iff better → commit). `compile_fn(module, current_state, trainset) -> dict`; `evaluate_holdout_fn(session, module, program_state|None, program_id|None) -> float`.
  - `gepa_compile(module, current_state, trainset) -> dict` (real GEPA), `evaluate_holdout(session, module, program_state, program_id) -> float` (real), `run_optimize(session, module) -> OptimizeResult` (wires the reals).

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_optimize.py
from bellwether.eval.optimize import promote_if_better, optimize, OptimizeResult
from bellwether.programs import save_program, set_champion, load_champion
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun
from sqlalchemy import select


def test_promote_if_better_strict():
    assert promote_if_better(0.8, 0.7) is True
    assert promote_if_better(0.7, 0.7) is False
    assert promote_if_better(0.6, 0.7) is False


def test_optimize_promotes_a_better_challenger(db_session):
    # no champion yet; fake compile returns a canned state; fake holdout: baseline 0.5, challenger 0.9
    def fake_compile(module, current_state, trainset): return {"trained": True}
    def fake_holdout(session, module, program_state, program_id):
        return 0.9 if program_state == {"trained": True} else 0.5  # challenger vs baseline

    res = optimize(db_session, "detect", compile_fn=fake_compile, evaluate_holdout_fn=fake_holdout)
    assert isinstance(res, OptimizeResult)
    assert res.version == 1 and abs(res.challenger_holdout - 0.9) < 1e-9 and res.promoted is True
    assert load_champion(db_session, "detect") == ({"trained": True}, 1)
    prog = db_session.execute(select(DspyProgram)).scalar_one()
    assert prog.holdout_score == 0.9 and prog.is_champion is True


def test_optimize_keeps_champion_when_challenger_not_better(db_session):
    champ = save_program(db_session, "detect", 1, {"old": True}, holdout_score=0.8, is_champion=True)
    db_session.flush()
    def fake_compile(module, current_state, trainset): return {"new": True}
    def fake_holdout(session, module, program_state, program_id): return 0.6  # worse than 0.8
    res = optimize(db_session, "detect", compile_fn=fake_compile, evaluate_holdout_fn=fake_holdout)
    assert res.promoted is False
    assert load_champion(db_session, "detect") == ({"old": True}, 1)  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/eval/test_optimize.py -v`
Expected: FAIL with `ImportError: cannot import name 'promote_if_better'`.

- [ ] **Step 3: Add the orchestration**

Append to `src/bellwether/eval/optimize.py`:
```python
from dataclasses import dataclass
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.detect import Detect, build_detector
from bellwether.llm.extract import Extract, build_extractor
from bellwether.models.dspy_program import DspyProgram
from bellwether.programs import load_champion, save_program, next_version, set_champion
from bellwether.eval.evaluate import evaluate_detect, evaluate_extract
from bellwether.eval.gepa_metric import detect_metric, extract_metric


@dataclass(frozen=True)
class OptimizeResult:
    module: str
    version: int
    challenger_holdout: float
    champion_holdout: float
    promoted: bool


def promote_if_better(challenger_holdout: float, champion_holdout: float) -> bool:
    return challenger_holdout > champion_holdout


def optimize(session, module, *, compile_fn, evaluate_holdout_fn) -> OptimizeResult:
    trainset = build_trainset(session, module, "train")
    champ = load_champion(session, module)
    current_state = champ[0] if champ else None

    compiled_state = compile_fn(module, current_state, trainset)
    version = next_version(session, module)
    program = save_program(session, module, version, compiled_state,
                           holdout_score=None, is_champion=False)

    challenger_holdout = evaluate_holdout_fn(session, module, compiled_state, program.id)
    program.holdout_score = challenger_holdout

    if champ is not None:
        champ_prog = session.execute(
            select(DspyProgram).where(DspyProgram.module == module, DspyProgram.is_champion.is_(True))
        ).scalar_one()
        champion_holdout = (champ_prog.holdout_score if champ_prog.holdout_score is not None
                            else evaluate_holdout_fn(session, module, champ_prog.artifact, champ_prog.id))
    else:
        champion_holdout = evaluate_holdout_fn(session, module, None, None)  # baseline

    promoted = promote_if_better(challenger_holdout, champion_holdout)
    if promoted:
        set_champion(session, program.id)
    session.commit()
    return OptimizeResult(module, version, challenger_holdout, champion_holdout, promoted)


def gepa_compile(module: str, current_state: dict | None, trainset) -> dict:
    settings = get_settings()
    mod = Detect() if module == "detect" else Extract()
    mod.set_lm(make_lm(settings.detect_model if module == "detect" else settings.extract_model))
    if current_state is not None:
        mod.load_state(current_state)
    metric = detect_metric if module == "detect" else extract_metric
    gepa = dspy.GEPA(metric=metric, auto=settings.gepa_auto,
                     reflection_lm=make_lm(settings.reflection_model))
    compiled = gepa.compile(mod, trainset=trainset, valset=trainset)
    return compiled.dump_state()


def evaluate_holdout(session, module: str, program_state: dict | None, program_id: int | None) -> float:
    if module == "detect":
        det = build_detector(program_state=program_state, version="challenger")
        return evaluate_detect(session, det, "holdout", dspy_program_id=program_id).score
    ext = build_extractor(program_state=program_state, version="challenger")
    return evaluate_extract(session, ext, "holdout", dspy_program_id=program_id).score


def run_optimize(session, module: str) -> OptimizeResult:
    return optimize(session, module, compile_fn=gepa_compile, evaluate_holdout_fn=evaluate_holdout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/eval/test_optimize.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/eval/optimize.py tests/eval/test_optimize.py
git commit -m "feat: GEPA optimize + champion/challenger promotion (strict holdout gate)"
```

---

### Task 10: Track B (minimal) + the firewall test

**Files:**
- Create: `src/bellwether/trackb/__init__.py` (empty), `src/bellwether/trackb/report.py`
- Test: `tests/test_firewall.py`, `tests/trackb/__init__.py` (empty), `tests/trackb/test_report.py`

**Interfaces:**
- Consumes: `Impact`, `Resolution`, `Extraction`, `Statement` (for the Track-B aggregation only).
- Produces: `avg_pct_move_by_figure(session) -> list[tuple[int, float]]` — average `measured` `pct_move` per `figure_id` (a minimal Track-B stand-in; the leaderboard is Plan 7).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_firewall.py
import inspect
import bellwether.eval.evaluate as ev
import bellwether.eval.metrics as me
import bellwether.eval.optimize as op
from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.llm.contracts import ExtractionResult
from bellwether.eval.evaluate import evaluate_extract


def test_eval_modules_do_not_reference_market_models():
    for mod in (ev, me, op):
        src = inspect.getsource(mod)
        assert "Impact" not in src, f"{mod.__name__} references Impact"
        assert "Resolution" not in src, f"{mod.__name__} references Resolution"


class StubExtractor:
    model = "stub"; version = "baseline"
    def extract(self, text): return ExtractionResult(["TSLA"], "up", "small", 0.5, "Tesla will grow")


def test_track_a_score_is_invariant_to_market_data(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="Tesla will grow next quarter.",
                   url=None, provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="small",
                    confidence=0.5, evidence_quote="Tesla will grow", model="m", version="baseline")
    db_session.add(ex); db_session.flush()
    db_session.add(ExtractionLabel(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="small",
                                   evidence_quote="Tesla will grow", source="review", split="holdout"))
    db_session.flush()
    before = evaluate_extract(db_session, StubExtractor(), "holdout").score
    # inject market data (Track B) — it must NOT change the Track-A score
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    db_session.add(Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="1d",
                          event_at=st.published_at, due_at=st.published_at, status="measured", pct_move=0.5))
    db_session.flush()
    after = evaluate_extract(db_session, StubExtractor(), "holdout").score
    assert before == after
```

```python
# tests/trackb/test_report.py
from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.trackb.report import avg_pct_move_by_figure


def test_avg_pct_move_by_figure(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=1); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=1)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="resolved"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="small", confidence=0.5,
                    evidence_quote="t", model="m", version="baseline"); db_session.add(ex); db_session.flush()
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    for pm in (0.2, 0.4):
        db_session.add(Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="1d",
                              event_at=st.published_at, due_at=st.published_at, status="measured", pct_move=pm))
    db_session.flush()
    result = dict(avg_pct_move_by_figure(db_session))
    assert abs(result[f.id] - 0.3) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_firewall.py tests/trackb/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: bellwether.trackb.report`.

- [ ] **Step 3: Write the Track-B report**

Create empty `src/bellwether/trackb/__init__.py` and empty `tests/trackb/__init__.py`, then:
```python
# src/bellwether/trackb/report.py
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact


def avg_pct_move_by_figure(session: Session) -> list[tuple[int, float]]:
    """Minimal Track-B aggregation: mean measured pct_move per figure. (Leaderboard is Plan 7.)"""
    rows = session.execute(
        select(Statement.figure_id, func.avg(Impact.pct_move))
        .join(Extraction, Extraction.statement_id == Statement.id)
        .join(Resolution, Resolution.extraction_id == Extraction.id)
        .join(Impact, Impact.resolution_id == Resolution.id)
        .where(Impact.status == "measured", Impact.pct_move.isnot(None))
        .group_by(Statement.figure_id)
    ).all()
    return [(fid, float(avg)) for fid, avg in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_firewall.py tests/trackb/test_report.py -v`
Expected: PASS (3 tests). The import-boundary test proves `eval/*` never references `Impact`/`Resolution`; the invariance test proves the Track-A score doesn't move when market data is added.

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/trackb tests/test_firewall.py tests/trackb
git commit -m "feat: minimal Track-B aggregation + firewall tests (invariance + import boundary)"
```

---

### Task 11: Review-and-correct API

**Files:**
- Create: `src/bellwether/api/review.py`
- Modify: `src/bellwether/api/schemas.py`, `src/bellwether/api/app.py`
- Test: `tests/api/test_review_api.py`

**Interfaces:**
- Consumes: `get_session`, `get_current_user`, `User`, `Statement`, `Extraction`, `Figure`, the label helpers (Task 4).
- Produces (schemas): `ExtractionCorrect{direction, magnitude, entities, evidence_quote}`, `ReviewSubmit{is_relevant: bool, extraction: ExtractionCorrect | None = None}`, `ReviewQueueItem{statement_id, text, figure_name, current_extraction: dict | None}`.
- Produces (router, all `Depends(get_current_user)`):
  - `GET /review/queue?module={extract|detect}&limit=` — statements owned by the caller that lack the relevant label (extract: `extracted`/`resolved` with no `extraction_labels` row; detect: any status with no `relevance_labels` row) → `list[ReviewQueueItem]`.
  - `POST /review/{statement_id}` (body `ReviewSubmit`) → 404 if the statement's figure is not owned; `is_relevant=false` writes a negative relevance label; `is_relevant=true` writes a positive relevance label plus an extraction label (from `extraction` if given, else copied from the statement's current `Extraction` = "confirm"); `422` if a corrected `evidence_quote` is not verbatim. Returns `{"ok": true}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_review_api.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.repositories.users import get_user_by_username


def _seed_extracted(db_session, owner_id, text="Tesla will grow production."):
    f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text=text, url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    db_session.add(Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                              confidence=0.5, evidence_quote="Tesla will grow", model="m", version="baseline"))
    db_session.flush()
    return st


def test_review_requires_auth(client):
    assert client.get("/review/queue?module=extract").status_code == 401


def test_queue_and_confirm(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    st = _seed_extracted(db_session, uid)
    q = client.get("/review/queue?module=extract", headers=auth_headers).json()
    assert any(item["statement_id"] == st.id for item in q)
    # confirm: no extraction body -> copies the model extraction to gold
    r = client.post(f"/review/{st.id}", json={"is_relevant": True}, headers=auth_headers)
    assert r.status_code == 200
    lab = db_session.execute(select(ExtractionLabel).where(ExtractionLabel.statement_id == st.id)).scalar_one()
    assert lab.direction == "up" and lab.entities == ["Tesla"]
    rel = db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st.id)).scalar_one()
    assert rel.is_relevant is True


def test_correct_and_reject_and_verbatim(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    st = _seed_extracted(db_session, uid)
    # correct: edit direction, valid verbatim quote
    r = client.post(f"/review/{st.id}", json={"is_relevant": True, "extraction": {
        "direction": "down", "magnitude": "moderate", "entities": ["TSLA"], "evidence_quote": "grow production"}},
        headers=auth_headers)
    assert r.status_code == 200
    lab = db_session.execute(select(ExtractionLabel).where(ExtractionLabel.statement_id == st.id)).scalar_one()
    assert lab.direction == "down" and lab.magnitude == "moderate"
    # non-verbatim gold quote -> 422
    bad = client.post(f"/review/{st.id}", json={"is_relevant": True, "extraction": {
        "direction": "up", "magnitude": "small", "entities": ["TSLA"], "evidence_quote": "Tesla will SHRINK"}},
        headers=auth_headers)
    assert bad.status_code == 422
    # reject -> negative relevance label, no extraction label change required
    st2 = _seed_extracted(db_session, uid, text="It was nice weather today.")
    rej = client.post(f"/review/{st2.id}", json={"is_relevant": False}, headers=auth_headers)
    assert rej.status_code == 200
    rel2 = db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st2.id)).scalar_one()
    assert rel2.is_relevant is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_review_api.py -v`
Expected: FAIL (route missing → 404s / import error).

- [ ] **Step 3: Add the schemas**

Append to `src/bellwether/api/schemas.py`:
```python
class ExtractionCorrect(BaseModel):
    direction: str
    magnitude: str
    entities: list[str]
    evidence_quote: str


class ReviewSubmit(BaseModel):
    is_relevant: bool
    extraction: ExtractionCorrect | None = None


class ReviewQueueItem(BaseModel):
    statement_id: int
    text: str
    figure_name: str
    current_extraction: dict | None
```

- [ ] **Step 4: Write the review router**

```python
# src/bellwether/api/review.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.labels import upsert_relevance_label, upsert_extraction_label
from bellwether.api.schemas import ReviewSubmit, ReviewQueueItem

router = APIRouter()


@router.get("/review/queue", response_model=list[ReviewQueueItem])
def review_queue(module: str = Query(pattern="^(extract|detect)$"),
                 limit: int = Query(default=50, ge=1, le=500),
                 session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    labelled = ExtractionLabel if module == "extract" else RelevanceLabel
    q = (select(Statement, Figure)
         .join(Figure, Figure.id == Statement.figure_id)
         .outerjoin(labelled, labelled.statement_id == Statement.id)
         .where(Figure.owner_id == user.id, labelled.id.is_(None)))
    if module == "extract":
        q = q.where(Statement.status.in_(("extracted", "resolved")))
    q = q.order_by(Statement.published_at.desc()).limit(limit)
    items = []
    for st, fig in session.execute(q).all():
        ex = session.execute(select(Extraction).where(Extraction.statement_id == st.id)).scalar_one_or_none()
        current = None if ex is None else {
            "entities": ex.entities, "direction": ex.direction, "magnitude": ex.magnitude,
            "confidence": ex.confidence, "evidence_quote": ex.evidence_quote,
        }
        items.append(ReviewQueueItem(statement_id=st.id, text=st.text, figure_name=fig.name,
                                     current_extraction=current))
    return items


@router.post("/review/{statement_id}")
def submit_review(statement_id: int, body: ReviewSubmit,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    st = session.execute(
        select(Statement).join(Figure, Figure.id == Statement.figure_id)
        .where(Statement.id == statement_id, Figure.owner_id == user.id)
    ).scalar_one_or_none()
    if st is None:
        raise HTTPException(status_code=404, detail="Statement not found")

    upsert_relevance_label(session, statement_id, body.is_relevant)
    if body.is_relevant:
        if body.extraction is not None:
            g = body.extraction
            entities, direction, magnitude, quote = g.entities, g.direction, g.magnitude, g.evidence_quote
        else:  # confirm: copy the model's current extraction as gold
            ex = session.execute(select(Extraction).where(Extraction.statement_id == statement_id)).scalar_one_or_none()
            if ex is None:
                raise HTTPException(status_code=422, detail="no extraction to confirm; provide one")
            entities, direction, magnitude, quote = ex.entities, ex.direction, ex.magnitude, ex.evidence_quote
        try:
            upsert_extraction_label(session, statement_id, entities, direction, magnitude, quote, st.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 5: Wire the router**

In `src/bellwether/api/app.py`, add `from bellwether.api.review import router as review_router` and `app.include_router(review_router)` inside `create_app`.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_review_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/bellwether/api/review.py src/bellwether/api/schemas.py src/bellwether/api/app.py tests/api/test_review_api.py
git commit -m "feat: review-and-correct API (queue + confirm/correct/reject -> golden labels)"
```

---

### Task 12: Programs / optimize / eval API

**Files:**
- Create: `src/bellwether/api/programs.py`, `src/bellwether/api/optimize_api.py`
- Modify: `src/bellwether/api/schemas.py`, `src/bellwether/api/app.py`
- Test: `tests/api/test_programs_optimize_api.py`

**Interfaces:**
- Consumes: `get_session`, `get_current_user`, `list_programs`/`set_champion` (Task 5), `run_optimize` (Task 9), `EvalRun`.
- Produces (schemas): `ProgramRead{id, module, version, holdout_score, is_champion}`, `EvalRunRead{id, module, split, metric, score, n}`, `OptimizeRead{module, version, challenger_holdout, champion_holdout, promoted}`.
- Produces (routers, authenticated):
  - `GET /programs?module=` → `list[ProgramRead]`.
  - `POST /programs/{program_id}/promote` → `ProgramRead`; 404 if missing.
  - `POST /optimize/{module}` (module in `detect`/`extract`) → `OptimizeRead` (calls `run_optimize`).
  - `GET /eval_runs?module=` → `list[EvalRunRead]` (newest first).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_programs_optimize_api.py
import bellwether.api.optimize_api as optimize_api
from bellwether.eval.optimize import OptimizeResult
from bellwether.programs import save_program
from bellwether.models.dspy_program import DspyProgram
from sqlalchemy import select


def test_programs_list_and_promote(client, auth_headers, db_session):
    p1 = save_program(db_session, "detect", 1, {"a": 1}, holdout_score=0.7, is_champion=False)
    p2 = save_program(db_session, "detect", 2, {"a": 2}, holdout_score=0.9, is_champion=False)
    db_session.flush()
    listed = client.get("/programs?module=detect", headers=auth_headers).json()
    assert [p["version"] for p in listed] == [2, 1]
    r = client.post(f"/programs/{p2.id}/promote", headers=auth_headers)
    assert r.status_code == 200 and r.json()["is_champion"] is True
    db_session.refresh(p2)
    assert p2.is_champion is True


def test_optimize_endpoint_uses_run_optimize(client, auth_headers, monkeypatch):
    # patch run_optimize so the endpoint runs no GEPA/LLM
    def fake_run_optimize(session, module):
        return OptimizeResult(module, 3, 0.95, 0.80, True)
    monkeypatch.setattr(optimize_api, "run_optimize", fake_run_optimize)
    r = client.post("/optimize/detect", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 3 and body["promoted"] is True and abs(body["challenger_holdout"] - 0.95) < 1e-9


def test_optimize_rejects_unknown_module(client, auth_headers):
    assert client.post("/optimize/bogus", headers=auth_headers).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/api/test_programs_optimize_api.py -v`
Expected: FAIL (routes/modules missing).

- [ ] **Step 3: Add the schemas**

Append to `src/bellwether/api/schemas.py`:
```python
class ProgramRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    module: str
    version: int
    holdout_score: float | None
    is_champion: bool


class EvalRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    module: str
    split: str
    metric: str
    score: float
    n: int


class OptimizeRead(BaseModel):
    module: str
    version: int
    challenger_holdout: float
    champion_holdout: float
    promoted: bool
```

- [ ] **Step 4: Write the routers**

```python
# src/bellwether/api/programs.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.eval_run import EvalRun
from bellwether.programs import list_programs, set_champion
from bellwether.api.schemas import ProgramRead, EvalRunRead

router = APIRouter()


@router.get("/programs", response_model=list[ProgramRead])
def get_programs(module: str | None = None, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    return list_programs(session, module)


@router.post("/programs/{program_id}/promote", response_model=ProgramRead)
def promote_program(program_id: int, session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    program = set_champion(session, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


@router.get("/eval_runs", response_model=list[EvalRunRead])
def get_eval_runs(module: str | None = None, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    q = select(EvalRun)
    if module is not None:
        q = q.where(EvalRun.module == module)
    return list(session.execute(q.order_by(EvalRun.created_at.desc(), EvalRun.id.desc())).scalars())
```

```python
# src/bellwether/api/optimize_api.py
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.eval.optimize import run_optimize
from bellwether.api.schemas import OptimizeRead

router = APIRouter()


@router.post("/optimize/{module}", response_model=OptimizeRead)
def optimize_module(module: str = Path(pattern="^(detect|extract)$"),
                    session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    result = run_optimize(session, module)
    return OptimizeRead(module=result.module, version=result.version,
                        challenger_holdout=result.challenger_holdout,
                        champion_holdout=result.champion_holdout, promoted=result.promoted)
```

- [ ] **Step 5: Wire the routers**

In `src/bellwether/api/app.py`, add imports for `programs` and `optimize_api` routers and `app.include_router(...)` both inside `create_app`.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/api/test_programs_optimize_api.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (Plans 1–5), pristine.
```bash
git add src/bellwether/api/programs.py src/bellwether/api/optimize_api.py src/bellwether/api/schemas.py src/bellwether/api/app.py tests/api/test_programs_optimize_api.py
git commit -m "feat: programs/optimize/eval_runs API (version history, promote, trigger optimize)"
```

---

## Self-Review

**Spec coverage (Plan 5):**
- Golden-label tables + eval_runs + dspy_programs — Task 1 ✓ (shared corpus, unique statement_id, unique module/version).
- Config (reflection_model, gepa_auto, holdout_modulus) — Task 2 ✓.
- Track-A metrics with GEPA feedback — Tasks 3, 8 ✓.
- Split + verbatim-guarded label upserts — Task 4 ✓.
- Program store (versioning + champion load/promote) — Task 5 ✓.
- Champion-loading seam (build_* load + version stamping) — Task 6 ✓ (the payoff of Plan 3's factories).
- Held-out evaluation (Track A) — Task 7 ✓.
- GEPA optimize + champion/challenger (strict holdout gate, rollback via flag) — Task 9 ✓; real GEPA (`gepa_compile`) wired, orchestration tested with a fake — ✓.
- Firewall (invariance + import boundary) + minimal Track-B — Task 10 ✓.
- Review-and-correct API — Task 11 ✓.
- Programs/optimize/eval API — Task 12 ✓.
- **Deferred with intent:** Track-B leaderboard (Plan 7), LLM-as-judge, optimizing Resolve, scheduled optimize — no task, correct.

**Deliberate test scoping (flag for reviewer):** real GEPA `compile` and the `/optimize` endpoint's live LLM path are **not** exercised in the suite (the orchestration is tested with a fake `compile_fn`/`evaluate_holdout_fn`, and the endpoint test monkeypatches `run_optimize`). A real optimize is a manual/live run — same discipline as the market adapter in Plan 4. Note this in the ledger for the final review + a live check.

**Placeholder scan:** every code step shows complete code; every command has expected output. No TBD/TODO.

**Type consistency:** `GoldExtraction(entities, direction, magnitude, evidence_quote)` (Task 3) is consumed identically in Tasks 7/8/10. `score_detection`/`score_extraction` return `(float, str)` throughout. `Detector`/`Extractor` gain `version: str` (Task 6) and stubs across Tasks 6/7/10 set it. `build_detector(lm, program_state, version)` / `build_extractor(...)` signatures (Task 6) match call sites in Task 9's `evaluate_holdout`. `optimize(session, module, *, compile_fn, evaluate_holdout_fn) -> OptimizeResult` (Task 9) matches `run_optimize` and the API in Task 12. `load_champion -> (artifact, version)` / `save_program` / `set_champion` / `next_version` / `list_programs` (Task 5) match Tasks 6/9/12. `EvalRun(module, dspy_program_id, split, metric, score, n)` (Task 1) matches the writes in Task 7. The review schemas (`ReviewSubmit`, `ExtractionCorrect`, `ReviewQueueItem`) and program/eval/optimize schemas (Task 12) match their routers.
