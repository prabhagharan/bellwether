from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert import Alert
from bellwether.api.stream import fetch_new_alerts
from bellwether.repositories.users import get_user_by_username, create_user


def _alert(db_session, owner_id):
    f = Figure(name="F", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline"); db_session.add(ex); db_session.flush()
    a = Alert(extraction_id=ex.id, rule_id=None, owner_id=owner_id, payload={"text": "hi"}, webhook_status="skipped")
    db_session.add(a); db_session.flush()
    return a


def test_fetch_new_alerts_owner_scoped(db_session):
    existing = get_user_by_username(db_session, "tester")
    tester = existing or create_user(db_session, "tester", "pw123")
    other_user = create_user(db_session, "other", "pw123")
    uid = tester.id
    a1 = _alert(db_session, uid)
    other = _alert(db_session, other_user.id)  # different owner
    rows = fetch_new_alerts(db_session, uid, after_id=0)
    ids = [r.id for r in rows]
    assert a1.id in ids and other.id not in ids
    assert fetch_new_alerts(db_session, uid, after_id=a1.id) == []   # nothing newer


def test_stream_requires_token(client):
    assert client.get("/stream").status_code in (401, 422)   # missing token
    assert client.get("/stream?token=bogus").status_code == 401
