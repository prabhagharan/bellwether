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
