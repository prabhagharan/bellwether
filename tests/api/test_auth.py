import pytest
from fastapi.testclient import TestClient
from bellwether.api.app import create_app
from bellwether.db import get_session
from bellwether.repositories.users import create_user


@pytest.fixture
def client(db_session):
    app = create_app()

    def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def a_user(db_session):
    create_user(db_session, "alice", "pw123")
    db_session.flush()


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


def test_inactive_user_cannot_login(client, db_session):
    from bellwether.repositories.users import get_user_by_username
    user = get_user_by_username(db_session, "alice")
    user.is_active = False
    db_session.flush()
    r = client.post("/auth/token", data={"username": "alice", "password": "pw123"})
    assert r.status_code == 401
