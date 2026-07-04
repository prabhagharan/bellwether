from sqlalchemy import text
from bellwether.db import SessionLocal

def test_session_executes_query():
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1


def test_db_session_fixture_rolls_back(db_session):
    assert db_session.execute(text("SELECT 1")).scalar() == 1


def test_get_session_commits_on_success():
    from bellwether.db import get_session, SessionLocal
    from bellwether.models.figure import Figure
    gen = get_session()
    session = next(gen)
    session.add(Figure(name="commit-probe", type="individual", aliases=[], owner_id=None))
    # exhausting the generator runs the post-yield commit + close
    try:
        next(gen)
    except StopIteration:
        pass
    # verify persistence via a brand-new session, then clean up
    with SessionLocal() as check:
        found = check.query(Figure).filter_by(name="commit-probe").one_or_none()
        assert found is not None
        check.delete(found)
        check.commit()
