from pathlib import Path
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.ingest import ingest_source

FEED = str(Path(__file__).parent.parent / "fixtures" / "sample_feed.xml")

def _seed_statements(db_session, owner_id):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=owner_id)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": FEED},
               provenance="primary", origin="manual", owner_id=owner_id, enabled=True)
    db_session.add(s); db_session.flush()
    ingest_source(db_session, s)
    return f

def test_statements_requires_auth(client):
    assert client.get("/statements").status_code == 401

def test_list_statements_filters(client, auth_headers, db_session):
    from sqlalchemy import select
    from bellwether.repositories.users import get_user_by_username
    from bellwether.models.statement import Statement
    uid = get_user_by_username(db_session, "tester").id
    f1 = _seed_statements(db_session, owner_id=uid)   # figure 1: 2 "new" statements
    f2 = _seed_statements(db_session, owner_id=uid)   # figure 2: its own source -> 2 more "new" statements
    # flip ONE of figure 1's statements to a different status
    stmt = db_session.execute(
        select(Statement).where(Statement.figure_id == f1.id)
    ).scalars().first()
    stmt.status = "reviewed"
    db_session.flush()

    # figure_id filter must exclude the other figure's statements
    body1 = client.get(f"/statements?figure_id={f1.id}", headers=auth_headers).json()
    assert len(body1) == 2
    assert {s["figure_id"] for s in body1} == {f1.id}

    # unfiltered returns statements from BOTH figures
    all_body = client.get("/statements", headers=auth_headers).json()
    assert len(all_body) == 4
    # newest first
    assert all(all_body[i]["published_at"] >= all_body[i + 1]["published_at"] for i in range(len(all_body) - 1))

    # status filter must genuinely narrow: 3 remain "new", 1 is "reviewed"
    new_body = client.get("/statements?status=new", headers=auth_headers).json()
    assert len(new_body) == 3 and all(s["status"] == "new" for s in new_body)
    reviewed_body = client.get("/statements?status=reviewed", headers=auth_headers).json()
    assert len(reviewed_body) == 1 and reviewed_body[0]["status"] == "reviewed"
