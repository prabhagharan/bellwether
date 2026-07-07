from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.alerts.contracts import NotifyOutcome
from bellwether.worker import make_alert_stage


class StubNotifier:
    def notify(self, webhook_url, payload): return NotifyOutcome(ok=True)


def _pending_ext(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="t", url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted"); db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=[], direction="up", magnitude="large", confidence=0.9,
                    evidence_quote="t", model="m", version="baseline", alert_status="pending")
    db_session.add(ex); db_session.flush()
    return ex


def test_alert_stage_processes_pending_extraction(db_session):
    ex = _pending_ext(db_session)
    db_session.add(AlertRule(owner_id=None, name="all", enabled=True, webhook_url=None, condition={}))
    db_session.flush()
    stage = make_alert_stage(StubNotifier())
    claimed = stage.claim_next(db_session)
    stage.process(db_session, claimed)
    db_session.refresh(ex)
    assert ex.alert_status == "done"
    assert db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().first() is not None
