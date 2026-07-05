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
