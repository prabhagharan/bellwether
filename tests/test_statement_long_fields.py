from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement


def test_statement_accepts_long_external_id_and_url(db_session):
    """Google News article ids/URLs exceed the old varchar(500)/varchar(1000) limits;
    external_id and url are now Text so a real news item persists (regression guard)."""
    f = Figure(name="F", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="news", config={"query": "F"},
               provenance="news", origin="auto", owner_id=None)
    db_session.add(s); db_session.flush()

    long_id = "C" * 600            # > old external_id varchar(500)
    long_url = "https://news.google.com/rss/articles/" + "A" * 1200  # > old url varchar(1000)
    st = Statement(figure_id=f.id, source_id=s.id, external_id=long_id, text="headline",
                   url=long_url, provenance="news",
                   published_at=datetime.now(timezone.utc), status="new")
    db_session.add(st)
    db_session.flush()             # would raise StringDataRightTruncation on the old schema

    db_session.refresh(st)
    assert st.external_id == long_id
    assert st.url == long_url
