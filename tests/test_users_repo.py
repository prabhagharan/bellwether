# tests/test_users_repo.py  (model-existence portion; repo added in Task 7)
from bellwether.models.user import User

def test_user_columns_exist():
    cols = set(User.__table__.columns.keys())
    assert {"id", "username", "hashed_password", "is_active", "created_at"} <= cols
