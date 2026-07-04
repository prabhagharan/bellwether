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
    # auth_headers created user "tester"; fetch its id to own the figure
    from bellwether.repositories.users import get_user_by_username
    uid = get_user_by_username(db_session, "tester").id
    f = _seed_statements(db_session, owner_id=uid)
    r = client.get(f"/statements?figure_id={f.id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all("text" in s and s["provenance"] == "primary" for s in body)
    # newest first
    assert body[0]["published_at"] >= body[1]["published_at"]
    # status filter
    assert all(s["status"] == "new" for s in client.get(f"/statements?status=new", headers=auth_headers).json())
