from datetime import datetime, timedelta, timezone
from bellwether.models.figure import Figure
from bellwether.queue import claim_pending_figure, reclaim_stale_figures


def _fig(db_session, status="pending"):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None, discovery_status=status)
    db_session.add(f); db_session.flush()
    return f


def test_claim_pending_figure(db_session):
    f = _fig(db_session)
    claimed = claim_pending_figure(db_session)
    assert claimed.id == f.id and claimed.discovery_status == "running" and claimed.discovery_claimed_at is not None
    assert claim_pending_figure(db_session) is None   # nothing left pending


def test_reclaim_stale_figures(db_session):
    f = _fig(db_session, status="running")
    f.discovery_claimed_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    db_session.flush()
    n = reclaim_stale_figures(db_session, "running", "pending", 300)
    assert n == 1
    db_session.refresh(f)
    assert f.discovery_status == "pending" and f.discovery_claimed_at is None
