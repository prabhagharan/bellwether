from datetime import datetime, timezone
import pytest
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.labels import split_for, upsert_relevance_label, upsert_extraction_label
from sqlalchemy import select


def _statement(db_session, text="Tesla will grow production."):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text=text, url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted")
    db_session.add(st); db_session.flush()
    return st


def test_split_for_deterministic():
    assert split_for(5) == "holdout" and split_for(10) == "holdout"
    assert split_for(4) == "train" and split_for(7) == "train"


def test_upsert_relevance_label_insert_then_update(db_session):
    st = _statement(db_session)
    lab = upsert_relevance_label(db_session, st.id, True)
    assert lab.is_relevant is True and lab.split in ("train", "holdout")
    lab2 = upsert_relevance_label(db_session, st.id, False)
    assert lab2.id == lab.id and lab2.is_relevant is False  # updated, not duplicated
    assert db_session.execute(select(RelevanceLabel).where(RelevanceLabel.statement_id == st.id)).scalars().all().__len__() == 1


def test_upsert_extraction_label_verbatim_guard(db_session):
    st = _statement(db_session, "Tesla will grow production this quarter.")
    lab = upsert_extraction_label(db_session, st.id, ["TSLA"], "up", "small", "Tesla will grow", st.text)
    assert lab.direction == "up"
    with pytest.raises(ValueError):
        upsert_extraction_label(db_session, st.id, ["TSLA"], "up", "small", "Tesla will SHRINK", st.text)
