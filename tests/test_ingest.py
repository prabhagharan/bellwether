from pathlib import Path
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.connectors.registry import build_connector, UnknownConnectorType
from bellwether.ingest import ingest_source, run_ingest_pass

FEED = str(Path(__file__).parent / "fixtures" / "sample_feed.xml")

def _rss_source(db_session, enabled=True):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": FEED},
               provenance="primary", origin="manual", owner_id=None, enabled=enabled)
    db_session.add(s); db_session.flush()
    return s

def test_ingest_source_creates_deduped_statements(db_session):
    s = _rss_source(db_session)
    new1 = ingest_source(db_session, s)
    assert len(new1) == 2
    assert {st.external_id for st in new1} == {"speech-0001", "speech-0002"}
    assert all(st.status == "new" and st.provenance == "primary" for st in new1)
    assert s.last_polled_at is not None
    # re-ingesting the same feed creates no duplicates
    new2 = ingest_source(db_session, s)
    assert new2 == []
    total = db_session.execute(select(func.count()).select_from(Statement)).scalar_one()
    assert total == 2

def test_build_connector_rejects_unknown_type(db_session):
    f = Figure(name="X", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="carrier_pigeon", config={}, provenance="primary",
               origin="manual", owner_id=None, enabled=True)
    db_session.add(s); db_session.flush()
    try:
        build_connector(s)
        assert False, "expected UnknownConnectorType"
    except UnknownConnectorType:
        pass

def test_run_ingest_pass_skips_unimplemented_and_disabled(db_session):
    rss = _rss_source(db_session)                       # enabled rss -> 2 statements
    f2 = Figure(name="Y", type="individual", aliases=[], owner_id=None); db_session.add(f2); db_session.flush()
    db_session.add(Source(figure_id=f2.id, connector_type="x", config={}, provenance="primary",
                          origin="manual", owner_id=None, enabled=True))   # unimplemented -> skipped
    disabled = _rss_source(db_session, enabled=False)                    # disabled -> skipped
    db_session.flush()
    count = run_ingest_pass(db_session)
    assert count == 2
