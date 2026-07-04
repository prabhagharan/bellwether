# tests/test_statements_repo.py
from datetime import datetime, timezone
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.repositories.statements import list_statements

def _seed(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    for i, (ext, when, st) in enumerate([
        ("a", datetime(2026, 7, 1, tzinfo=timezone.utc), "new"),
        ("b", datetime(2026, 7, 3, tzinfo=timezone.utc), "new"),
        ("c", datetime(2026, 7, 2, tzinfo=timezone.utc), "extracted"),
    ]):
        db_session.add(Statement(figure_id=f.id, source_id=s.id, external_id=ext, text=ext,
                                 url=None, provenance="primary", published_at=when, status=st))
    db_session.flush()
    return f

def test_list_orders_newest_first_and_filters(db_session):
    f = _seed(db_session)
    all_stmts = list_statements(db_session, figure_id=f.id)
    assert [s.external_id for s in all_stmts] == ["b", "c", "a"]   # newest published_at first
    new_only = list_statements(db_session, figure_id=f.id, status="new")
    assert {s.external_id for s in new_only} == {"a", "b"}

def test_list_respects_limit(db_session):
    f = _seed(db_session)
    assert len(list_statements(db_session, figure_id=f.id, limit=1)) == 1
