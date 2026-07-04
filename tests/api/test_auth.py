import pytest
from fastapi.testclient import TestClient
from bellwether.api.app import create_app
from bellwether.db import SessionLocal
from bellwether.repositories.users import get_user_by_username, create_user


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def a_user():
    # This fixture commits through a real SessionLocal (not the rollback-based
    # `db_session` fixture used elsewhere), because the app's own `get_session`
    # dependency opens independent DB sessions per request, invisible to any
    # savepoint-based rollback. That real commit would otherwise leave `alice`
    # permanently in the shared dev DB, breaking tests/test_seed.py's "table
    # is empty" assumption on later runs -- so we tear it down here too.
    with SessionLocal() as s:
        created_here = get_user_by_username(s, "alice") is None
        if created_here:
            create_user(s, "alice", "pw123")
            s.commit()
    yield
    if created_here:
        with SessionLocal() as s:
            user = get_user_by_username(s, "alice")
            if user is not None:
                s.delete(user)
                s.commit()


def test_login_returns_token(client):
    r = client.post("/auth/token", data={"username": "alice", "password": "pw123"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_bad_password_rejected(client):
    r = client.post("/auth/token", data={"username": "alice", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/me").status_code == 401


def test_me_with_token(client):
    token = client.post("/auth/token", data={"username": "alice", "password": "pw123"}).json()["access_token"]
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "alice"
