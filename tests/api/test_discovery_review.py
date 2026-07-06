from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.user import User
from bellwether.repositories.users import get_user_by_username


def _pending_source(db_session, owner_id):
    f = Figure(name="F", type="individual", aliases=[], owner_id=owner_id, discovery_status="done")
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="x", config={"handle": "fed"}, provenance="primary",
               origin="discovered", enabled=False, status="pending_review", verified=False,
               discovery_confidence=0.6, discovery_meta={"wikidata": True}, owner_id=owner_id)
    db_session.add(s); db_session.flush()
    return s


def test_queue_requires_auth(client):
    assert client.get("/discovery/queue").status_code == 401


def test_queue_and_confirm(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    s = _pending_source(db_session, uid)
    q = client.get("/discovery/queue", headers=auth_headers).json()
    assert any(i["source_id"] == s.id for i in q)
    r = client.post(f"/discovery/{s.id}", json={"decision": "confirm"}, headers=auth_headers)
    assert r.status_code == 200
    db_session.refresh(s)
    assert s.status == "active" and s.enabled is True and s.verified is True


def test_reject_and_ownership(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    s = _pending_source(db_session, uid)
    assert client.post(f"/discovery/{s.id}", json={"decision": "reject"}, headers=auth_headers).status_code == 200
    db_session.refresh(s)
    assert s.status == "rejected" and s.enabled is False
    # other user's source -> 404
    other = User(username="other", hashed_password="x", is_active=True); db_session.add(other); db_session.flush()
    s2 = _pending_source(db_session, other.id)
    assert client.post(f"/discovery/{s2.id}", json={"decision": "confirm"}, headers=auth_headers).status_code == 404
    # bad decision -> 422
    assert client.post(f"/discovery/{s.id}", json={"decision": "maybe"}, headers=auth_headers).status_code == 422
