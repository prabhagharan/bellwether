# tests/test_queue_impacts.py
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import delete
from bellwether.db import SessionLocal
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.queue import claim_due_impact, reclaim_stale_impacts


def _clear():
    with SessionLocal() as s:
        for m in (Impact, Resolution, Extraction, Detection, Statement, Source, Figure, User):
            s.execute(delete(m))
        s.commit()


@pytest.fixture
def clean_db():
    _clear(); yield; _clear()


def _impact(due_offset_seconds, status="pending"):
    """Create the FK chain and one impact whose due_at is now + offset. Returns impact id."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        f = Figure(name="F", type="individual", aliases=[], owner_id=None); s.add(f); s.flush()
        src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                     origin="manual", owner_id=None); s.add(src); s.flush()
        st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text="t", url=None,
                       provenance="primary", published_at=now, status="resolved"); s.add(st); s.flush()
        ex = Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                        confidence=0.5, evidence_quote="t", model="m", version="baseline"); s.add(ex); s.flush()
        r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity",
                       measurable=True); s.add(r); s.flush()
        imp = Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="5m",
                     event_at=now, due_at=now + timedelta(seconds=due_offset_seconds), status=status)
        s.add(imp); s.commit()
        return imp.id


def test_claims_due_but_not_future(clean_db):
    due_id = _impact(-60)      # due 1 min ago
    _impact(3600)              # due in 1h -> must NOT be claimed
    with SessionLocal() as s:
        claimed = claim_due_impact(s)
        assert claimed is not None and claimed.id == due_id and claimed.status == "measuring"
        assert claimed.claimed_at is not None
    with SessionLocal() as s:
        assert claim_due_impact(s) is None   # only the future one remains


def test_reclaim_stale_impacts(clean_db):
    iid = _impact(-60, status="measuring")
    with SessionLocal() as s:
        s.get(Impact, iid).claimed_at = datetime(2000, 1, 1, tzinfo=timezone.utc); s.commit()
    with SessionLocal() as s:
        assert reclaim_stale_impacts(s, "measuring", "pending", 300) == 1
    with SessionLocal() as s:
        assert s.get(Impact, iid).status == "pending"
