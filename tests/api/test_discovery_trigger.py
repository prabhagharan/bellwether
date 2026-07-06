# tests/api/test_discovery_trigger.py
from sqlalchemy import select
from bellwether.models.figure import Figure


def test_create_figure_defaults_to_discovery_pending(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Jerome Powell", "type": "individual"}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["discovery_status"] == "pending"


def test_create_figure_discover_false_skips(client, auth_headers, db_session):
    r = client.post("/figures", json={"name": "Manual Co", "type": "org", "discover": False}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["discovery_status"] == "skipped"


def test_retrigger_discovery(client, auth_headers, db_session):
    fid = client.post("/figures", json={"name": "X", "type": "individual", "discover": False},
                      headers=auth_headers).json()["id"]
    r = client.post(f"/figures/{fid}/discover", headers=auth_headers)
    assert r.status_code == 200 and r.json()["discovery_status"] == "pending"
    assert client.post("/figures/999999/discover", headers=auth_headers).status_code == 404
