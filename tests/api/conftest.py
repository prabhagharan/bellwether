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


@pytest.fixture
def auth_headers(client, db_session):
    create_user(db_session, "tester", "pw123")
    db_session.flush()
    token = client.post("/auth/token", data={"username": "tester", "password": "pw123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
