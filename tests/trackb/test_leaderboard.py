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
    f = _chain(db_session, None, "up", 0.5)      # predicted up, moved up -> hit
    _chain_same = Statement  # noqa
    # second impact for the same figure: predicted up, moved down -> miss
    _chain(db_session, None, "up", -0.3)
    rows = {r["figure_id"]: r for r in leaderboard_by_figure(db_session, owner_id=None)}
    # two different figures created (helper makes a new figure each call) — assert both present with hit-rates
    assert all(r["n"] == 1 for r in rows.values())
    hit = [r for r in rows.values() if r["directional_hit_rate"] == 1.0]
    miss = [r for r in rows.values() if r["directional_hit_rate"] == 0.0]
    assert len(hit) == 1 and len(miss) == 1
