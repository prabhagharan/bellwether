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
