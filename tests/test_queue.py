# tests/test_queue.py
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import delete, select
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.queue import claim_one, reclaim_stale


def _clear():
    with SessionLocal() as s:
        for m in (Statement, Source, Figure, User):
            s.execute(delete(m))
        s.commit()


@pytest.fixture
def clean_db():
    _clear()
    yield
    _clear()


def _seed(n, status="new"):
    """Create n statements (oldest first) and return their ids in publish order."""
    with SessionLocal() as s:
        f = Figure(name="F", type="individual", aliases=[], owner_id=None)
        s.add(f); s.flush()
        src = Source(figure_id=f.id, connector_type="rss", config={},
                     provenance="primary", origin="manual", owner_id=None)
        s.add(src); s.flush()
        ids = []
        for i in range(n):
            st = Statement(
                figure_id=f.id, source_id=src.id, external_id=f"e{i}", text=f"t{i}",
                url=None, provenance="primary",
                published_at=datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
                status=status,
            )
            s.add(st); s.flush()
            ids.append(st.id)
        s.commit()
        return ids


def test_claim_one_takes_oldest_and_flips_status(clean_db):
    ids = _seed(2)  # ids[0] is oldest by published_at
    with SessionLocal() as s:
        claimed = claim_one(s, "new", "detecting")
        assert claimed is not None
        assert claimed.id == ids[0]
        assert claimed.status == "detecting"
        assert claimed.claimed_at is not None
    # a second claim takes the next row; a third finds nothing
    with SessionLocal() as s:
        second = claim_one(s, "new", "detecting")
        assert second.id == ids[1]
    with SessionLocal() as s:
        assert claim_one(s, "new", "detecting") is None


def test_claim_one_skips_locked_rows(clean_db):
    ids = _seed(1)
    sa = SessionLocal()
    # session A locks the only 'new' row with FOR UPDATE SKIP LOCKED, does NOT commit
    locked = sa.execute(
        select(Statement).where(Statement.status == "new")
        .with_for_update(skip_locked=True).limit(1)
    ).scalar_one()
    assert locked.id == ids[0]
    try:
        # session B must skip the locked row -> nothing to claim
        with SessionLocal() as sb:
            assert claim_one(sb, "new", "detecting") is None
    finally:
        sa.rollback(); sa.close()


def test_reclaim_stale_resets_old_in_flight_rows(clean_db):
    ids = _seed(1, status="detecting")
    # backdate claimed_at so it is older than the cutoff
    with SessionLocal() as s:
        st = s.get(Statement, ids[0])
        st.claimed_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        s.commit()
    with SessionLocal() as s:
        n = reclaim_stale(s, "detecting", "new", older_than_seconds=300)
        assert n == 1
    with SessionLocal() as s:
        st = s.get(Statement, ids[0])
        assert st.status == "new" and st.claimed_at is None
