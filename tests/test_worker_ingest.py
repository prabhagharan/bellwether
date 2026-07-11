from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.worker import make_ingest_stage, main
import bellwether.ingest as ingest_mod

FEED = str(Path(__file__).parent / "fixtures" / "sample_feed.xml")


def _rss_source(db_session, *, enabled=True, last_polled=None, interval=300, feed=FEED):
    f = Figure(name="Chair", type="central_bank", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={"feed_url": feed},
               provenance="primary", origin="manual", owner_id=None,
               enabled=enabled, poll_interval_seconds=interval, last_polled_at=last_polled)
    db_session.add(s); db_session.flush()
    return s


def test_ingest_stage_polls_due_source(db_session):
    s = _rss_source(db_session, last_polled=None)
    stage = make_ingest_stage()
    row = stage.claim_next(db_session)
    assert row is not None and row.id == s.id
    stage.process(db_session, row)
    stmts = db_session.execute(select(Statement).where(Statement.source_id == s.id)).scalars().all()
    assert len(stmts) == 2 and all(st.status == "new" for st in stmts)


def test_ingest_stage_skips_not_due_source(db_session):
    _rss_source(db_session, last_polled=datetime.now(timezone.utc) - timedelta(seconds=5), interval=300)
    stage = make_ingest_stage()
    assert stage.claim_next(db_session) is None


def test_ingest_stage_backs_off_on_fetch_error(db_session, monkeypatch):
    class Boom:
        def fetch(self):
            raise RuntimeError("feed 500")
    monkeypatch.setattr(ingest_mod, "build_connector", lambda source: Boom())
    s = _rss_source(db_session, last_polled=None)
    stage = make_ingest_stage()
    row = stage.claim_next(db_session)                 # claim stamps + commits last_polled_at
    polled_after_claim = row.last_polled_at
    assert polled_after_claim is not None
    with pytest.raises(RuntimeError):                  # run_worker's loop would catch+log this
        stage.process(db_session, row)
    db_session.rollback()                              # mimic run_worker rolling back the failed process
    db_session.refresh(s)
    n = db_session.execute(select(func.count()).select_from(Statement).where(Statement.source_id == s.id)).scalar_one()
    assert n == 0                                      # no statements from the failed fetch
    assert s.last_polled_at is not None                # timer stayed advanced -> backs off one interval


def test_cli_accepts_ingest_stage(monkeypatch):
    captured = {}
    def fake_run_worker(stage, **kwargs):
        captured["stage_name"] = stage.name
        return 0
    monkeypatch.setattr("bellwether.worker.run_worker", fake_run_worker)
    main(["ingest", "--once"])
    assert captured["stage_name"] == "ingest"


def test_cli_rejects_unknown_stage(monkeypatch):
    monkeypatch.setattr("bellwether.worker.run_worker", lambda stage, **kw: 0)
    with pytest.raises(SystemExit):
        main(["carrier_pigeon"])
