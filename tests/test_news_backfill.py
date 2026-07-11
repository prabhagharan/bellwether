from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.repositories import watchlist as repo


def _figure(db_session, name="Powell"):
    f = Figure(name=name, type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    return f


def test_create_news_source(db_session):
    f = _figure(db_session, "Jerome Powell")
    src = repo.create_news_source(db_session, f, owner_id=None)
    assert src.connector_type == "news"
    assert src.config["query"] == "Jerome Powell"
    assert src.enabled is True
    assert src.status == "active"
    assert src.origin == "auto"
    assert src.provenance == "news"
    assert src.poll_interval_seconds == 1800


def test_backfill_creates_missing_and_skips_existing(db_session):
    f1 = _figure(db_session, "A")                         # no news source
    f2 = _figure(db_session, "B")
    repo.create_news_source(db_session, f2, owner_id=None)  # already has one
    created = repo.backfill_news_sources(db_session)
    assert created == 1                                   # only f1 got one
    for fid in (f1.id, f2.id):
        news = db_session.execute(
            select(Source).where(Source.figure_id == fid, Source.connector_type == "news")
        ).scalars().all()
        assert len(news) == 1                             # exactly one each, no dup
