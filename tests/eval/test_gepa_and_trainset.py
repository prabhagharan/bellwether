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
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="rates rise", url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="detected")
    db_session.add(st); db_session.flush()
    db_session.add(RelevanceLabel(statement_id=st.id, is_relevant=True, source="review", split="train"))
    db_session.flush()
    ts = build_trainset(db_session, "detect", "train")
    assert len(ts) == 1 and ts[0].statement_text == "rates rise" and ts[0].is_relevant is True
    assert set(ts[0].inputs().keys()) == {"statement_text"}
