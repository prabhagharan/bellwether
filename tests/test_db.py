from sqlalchemy import text
from bellwether.db import SessionLocal

def test_session_executes_query():
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1
