from sqlalchemy import select
from bellwether.seed import seed_admin
from bellwether.models.user import User
from bellwether.config import get_settings


def test_seeds_admin_when_empty(db_session, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_USERNAME", "root")
    monkeypatch.setenv("ADMIN_PASSWORD", "rootpw")
    created = seed_admin(db_session)
    assert created is not None and created.username == "root"


def test_seed_is_idempotent(db_session):
    from bellwether.repositories.users import create_user
    create_user(db_session, "someone", "pw")
    assert seed_admin(db_session) is None
    count = db_session.execute(select(User)).scalars().all()
    assert len(count) == 1
