from bellwether.models.user import User
from bellwether.models.alert_rule import AlertRule


def test_requires_auth(client):
    assert client.get("/alert_rules").status_code == 401


def test_other_owners_rule_is_invisible_and_404(client, auth_headers, db_session):
    """A rule owned by a different user is never listed and 404s on PATCH/DELETE (no leak)."""
    other = User(username="other_rules_user", hashed_password="x", is_active=True)
    db_session.add(other)
    db_session.flush()
    rule = AlertRule(owner_id=other.id, name="theirs", condition={}, webhook_url=None, enabled=True)
    db_session.add(rule)
    db_session.flush()
    listed = client.get("/alert_rules", headers=auth_headers).json()
    assert all(x["id"] != rule.id for x in listed)
    assert client.patch(f"/alert_rules/{rule.id}", json={"enabled": False}, headers=auth_headers).status_code == 404
    assert client.delete(f"/alert_rules/{rule.id}", headers=auth_headers).status_code == 404


def test_crud_owner_scoped(client, auth_headers, db_session):
    body = {"name": "strong up", "condition": {"min_confidence": 0.7, "directions": ["up"]},
            "webhook_url": "http://hook", "enabled": True}
    r = client.post("/alert_rules", json=body, headers=auth_headers)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert r.json()["condition"]["min_confidence"] == 0.7
    listed = client.get("/alert_rules", headers=auth_headers).json()
    assert any(x["id"] == rid for x in listed)
    p = client.patch(f"/alert_rules/{rid}", json={"enabled": False}, headers=auth_headers)
    assert p.status_code == 200 and p.json()["enabled"] is False
    d = client.delete(f"/alert_rules/{rid}", headers=auth_headers)
    assert d.status_code == 204
    assert client.patch("/alert_rules/999999", json={"enabled": False}, headers=auth_headers).status_code == 404


def test_condition_rejects_unknown_keys(client, auth_headers):
    bad = {"name": "x", "condition": {"bogus_field": 1}, "webhook_url": None, "enabled": True}
    assert client.post("/alert_rules", json=bad, headers=auth_headers).status_code == 422
