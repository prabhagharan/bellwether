# tests/test_users_repo.py  (model-existence portion; repo added in Task 7)
from bellwether.models.user import User
from bellwether.repositories.users import create_user, get_user_by_username
from bellwether.security.passwords import verify_password

def test_user_columns_exist():
    cols = set(User.__table__.columns.keys())
    assert {"id", "username", "hashed_password", "is_active", "created_at"} <= cols

def test_create_and_fetch_user(db_session):
    user = create_user(db_session, "bob", "pw123")
    assert user.id is not None
    assert user.username == "bob"
    assert verify_password("pw123", user.hashed_password)
    fetched = get_user_by_username(db_session, "bob")
    assert fetched is not None and fetched.id == user.id

def test_get_missing_user_returns_none(db_session):
    assert get_user_by_username(db_session, "nobody") is None
