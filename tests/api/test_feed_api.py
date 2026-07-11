from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact


def test_feed_requires_auth(client):
    assert client.get("/signals").status_code == 401
    assert client.get("/impacts").status_code == 401
    assert client.get("/leaderboard").status_code == 401


def _seed_chain(db_session, owner_id):
    f = Figure(name="OtherFig", type="individual", aliases=[], owner_id=owner_id)
    db_session.add(f)
    db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s)
    db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="resolved")
    db_session.add(st)
    db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="up", magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline")
    db_session.add(ex)
    db_session.flush()
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r)
    db_session.flush()
    db_session.add(Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="1d",
                          event_at=st.published_at, due_at=st.published_at, status="measured", pct_move=0.5))
    db_session.flush()
    return f, ex


def test_feed_excludes_other_owners_data(client, auth_headers, db_session):
    """/signals, /impacts, /leaderboard never surface another owner's rows (no leak)."""
    other = User(username="other_feed_user", hashed_password="x", is_active=True)
    db_session.add(other)
    db_session.flush()
    f, ex = _seed_chain(db_session, other.id)
    signals = client.get("/signals", headers=auth_headers).json()
    assert all(x["id"] != ex.id for x in signals)
    leaderboard = client.get("/leaderboard", headers=auth_headers).json()
    assert all(row["figure_id"] != f.id for row in leaderboard)
    # the tester owns no chains, so their impacts feed is empty (the other owner's impact excluded)
    assert client.get("/impacts", headers=auth_headers).json() == []


def test_signals_include_source_context(client, auth_headers, db_session):
    """/signals carries the underlying source context: headline, url, source type,
    figure name, published date, and the evidence quote."""
    tester = db_session.execute(select(User).where(User.username == "tester")).scalar_one()
    f = Figure(name="Donald Trump", type="individual", aliases=[], owner_id=tester.id)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="news", config={"query": "Donald Trump"},
               provenance="news", origin="auto", owner_id=tester.id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e1",
                   text="Trump signals 25% EV tariffs", url="https://news.example/article",
                   provenance="news", published_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
                   status="resolved")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["TSLA"], direction="down", magnitude="moderate",
                    confidence=0.72, evidence_quote="tariffs will crush margins",
                    model="m", version="baseline")
    db_session.add(ex); db_session.flush()

    rows = client.get("/signals", headers=auth_headers).json()
    row = next(x for x in rows if x["id"] == ex.id)
    assert row["text"] == "Trump signals 25% EV tariffs"
    assert row["url"] == "https://news.example/article"
    assert row["source_type"] == "news"
    assert row["figure_name"] == "Donald Trump"
    assert row["published_at"].startswith("2026-07-11")
    assert row["evidence_quote"] == "tariffs will crush margins"
    # existing fields still present
    assert row["direction"] == "down" and row["magnitude"] == "moderate"
    assert row["entities"] == ["TSLA"]
