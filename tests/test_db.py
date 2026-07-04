from sqlalchemy import text
from bellwether.db import SessionLocal

def test_session_executes_query():
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_db_session_fixture_rolls_back(db_session):
    assert db_session.execute(text("SELECT 1")).scalar() == 1
