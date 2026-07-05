from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.user import User
from bellwether.repositories.users import get_user_by_username


def _seed_extracted(db_session, owner_id, text="Tesla will grow production."):
    f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text=text, url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    db_session.add(Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                              confidence=0.5, evidence_quote="Tesla will grow", model="m", version="baseline"))
    db_session.flush()
    return st


def _seed_statement(db_session, owner_id, status, text="Rates will rise."):
    f = Figure(name="Jerome Powell", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text=text, url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status=status); db_session.add(st); db_session.flush()
    return st


def test_review_requires_auth(client):
    assert client.get("/review/queue?module=extract").status_code == 401


def test_detect_queue_excludes_undetected_statements(client, auth_headers, db_session):
    """The detect queue lists processed-but-unlabeled statements, never pre-detect ones (spec §5)."""
    uid = get_user_by_username(db_session, "tester").id
    undetected = _seed_statement(db_session, uid, status="new", text="Not yet detected.")
    detected = _seed_statement(db_session, uid, status="detected", text="Already detected, needs a label.")
    ids = {item["statement_id"] for item in client.get("/review/queue?module=detect", headers=auth_headers).json()}
    assert detected.id in ids          # processed + unlabeled -> in the queue
    assert undetected.id not in ids    # status="new" -> excluded


def test_queue_and_confirm(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    st = _seed_extracted(db_session, uid)
    q = client.get("/review/queue?module=extract", headers=auth_headers).json()
    assert any(item["statement_id"] == st.id for item in q)
    # confirm: no extraction body -> copies the model extraction to gold
    r = client.post(f"/review/{st.id}", json={"is_relevant": True}, headers=auth_headers)
    assert r.status_code == 200
    lab = db_session.execute(select(ExtractionLabel).where(ExtractionLabel.statement_id == st.id)).scalar_one()
    assert lab.direction == "up" and lab.entities == ["Tesla"]
    rel = db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st.id)).scalar_one()
    assert rel.is_relevant is True


def test_correct_and_reject_and_verbatim(client, auth_headers, db_session):
    uid = get_user_by_username(db_session, "tester").id
    st = _seed_extracted(db_session, uid)
    # correct: edit direction, valid verbatim quote
    r = client.post(f"/review/{st.id}", json={"is_relevant": True, "extraction": {
        "direction": "down", "magnitude": "moderate", "entities": ["TSLA"], "evidence_quote": "grow production"}},
        headers=auth_headers)
    assert r.status_code == 200
    lab = db_session.execute(select(ExtractionLabel).where(ExtractionLabel.statement_id == st.id)).scalar_one()
    assert lab.direction == "down" and lab.magnitude == "moderate"
    # non-verbatim gold quote -> 422
    bad = client.post(f"/review/{st.id}", json={"is_relevant": True, "extraction": {
        "direction": "up", "magnitude": "small", "entities": ["TSLA"], "evidence_quote": "Tesla will SHRINK"}},
        headers=auth_headers)
    assert bad.status_code == 422
    # reject -> negative relevance label, no extraction label change required
    st2 = _seed_extracted(db_session, uid, text="It was nice weather today.")
    rej = client.post(f"/review/{st2.id}", json={"is_relevant": False}, headers=auth_headers)
    assert rej.status_code == 200
    rel2 = db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st2.id)).scalar_one()
    assert rel2.is_relevant is False


def test_owner_scoping(client, auth_headers, db_session):
    """Statement owned by different user is invisible to tester."""
    other = User(username="other_user", hashed_password="x", is_active=True)
    db_session.add(other)
    db_session.flush()
    st = _seed_extracted(db_session, other.id)
    # other user's statement should not appear in tester's queue
    q = client.get("/review/queue?module=extract", headers=auth_headers).json()
    assert not any(item["statement_id"] == st.id for item in q)
    # POST to other user's statement should return 404
    r = client.post(f"/review/{st.id}", json={"is_relevant": True}, headers=auth_headers)
    assert r.status_code == 404
