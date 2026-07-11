from datetime import datetime, timedelta, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.queue import claim_due_source


def _source(db_session, *, enabled=True, last_polled=None, interval=300):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": "http://x/feed"},
               provenance="primary", origin="manual", owner_id=None,
               enabled=enabled, poll_interval_seconds=interval, last_polled_at=last_polled)
    db_session.add(s); db_session.flush()
    return s


def test_never_polled_source_is_due(db_session):
    s = _source(db_session, last_polled=None)
    claimed = claim_due_source(db_session)
    assert claimed is not None and claimed.id == s.id
    assert claimed.last_polled_at is not None            # timer stamped at claim


def test_recently_polled_source_not_due(db_session):
    _source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=10), interval=300)
    assert claim_due_source(db_session) is None


def test_past_interval_source_is_due_again(db_session):
    s = _source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=400), interval=300)
    claimed = claim_due_source(db_session)
    assert claimed is not None and claimed.id == s.id


def test_disabled_source_never_claimed(db_session):
    _source(db_session, enabled=False, last_polled=None)
    assert claim_due_source(db_session) is None


def test_claim_advances_the_timer(db_session):
    s = _source(db_session, last_polled=None)
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    claim_due_source(db_session)
    db_session.refresh(s)
    assert s.last_polled_at >= before                    # advanced to ~now, so no longer due
    assert claim_due_source(db_session) is None          # not due immediately after
