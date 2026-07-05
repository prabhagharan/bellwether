from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.trackb.report import avg_pct_move_by_figure


def test_avg_pct_move_by_figure(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="resolved"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="small", confidence=0.5,
                    evidence_quote="t", model="m", version="baseline"); db_session.add(ex); db_session.flush()
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    for window, pm in [("1d", 0.2), ("2d", 0.4)]:
        db_session.add(Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window=window,
                              event_at=st.published_at, due_at=st.published_at, status="measured", pct_move=pm))
    db_session.flush()
    result = dict(avg_pct_move_by_figure(db_session))
    assert abs(result[f.id] - 0.3) < 1e-9
