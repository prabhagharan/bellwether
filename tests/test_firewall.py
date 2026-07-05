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
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
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
