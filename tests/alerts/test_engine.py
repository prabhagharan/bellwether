# tests/alerts/test_engine.py
from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.models.user import User
from bellwether.alerts.contracts import NotifyOutcome
from bellwether.alerts.engine import evaluate_extraction


class SentNotifier:
    def __init__(self, ok=True): self.ok, self.calls = ok, []
    def notify(self, webhook_url, payload): self.calls.append((webhook_url, payload)); return NotifyOutcome(ok=self.ok)


def _extraction(db_session, owner_id=None, direction="up", magnitude="large", confidence=0.9):
    f = Figure(name="Fed", type="individual", aliases=[], owner_id=owner_id); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=owner_id)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="Rates will rise sharply.", url="http://x/1",
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="extracted")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=["Fed"], direction=direction, magnitude=magnitude,
                    confidence=confidence, evidence_quote="Rates will rise", model="m", version="baseline")
    db_session.add(ex); db_session.flush()
    return f, ex


def test_matching_rule_creates_alert_and_dispatches(db_session):
    f, ex = _extraction(db_session)
    db_session.add(AlertRule(owner_id=None, name="strong up", enabled=True, webhook_url="http://hook",
                             condition={"min_confidence": 0.7, "min_magnitude": "moderate", "directions": ["up"]}))
    db_session.flush()
    notifier = SentNotifier(ok=True)
    evaluate_extraction(db_session, ex, notifier)
    db_session.flush()
    alert = db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalar_one()
    assert alert.webhook_status == "sent" and alert.sent_at is not None
    assert alert.payload["figure"] == "Fed" and alert.payload["direction"] == "up"
    assert len(notifier.calls) == 1 and notifier.calls[0][0] == "http://hook"


def test_non_matching_rule_creates_no_alert(db_session):
    f, ex = _extraction(db_session, direction="up")
    db_session.add(AlertRule(owner_id=None, name="downs only", enabled=True, webhook_url=None,
                             condition={"directions": ["down"]}))
    db_session.flush()
    evaluate_extraction(db_session, ex, SentNotifier())
    db_session.flush()
    assert db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().first() is None


def test_failed_webhook_recorded_and_idempotent(db_session):
    f, ex = _extraction(db_session)
    db_session.add(AlertRule(owner_id=None, name="r", enabled=True, webhook_url="http://hook", condition={}))
    db_session.flush()
    evaluate_extraction(db_session, ex, SentNotifier(ok=False))
    db_session.flush()
    a = db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalar_one()
    assert a.webhook_status == "failed"
    # re-run must not create a duplicate (unique extraction_id, rule_id)
    evaluate_extraction(db_session, ex, SentNotifier(ok=True))
    db_session.flush()
    assert len(db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().all()) == 1


def test_owner_scoping_excludes_other_owners_rules(db_session):
    """A real-owner figure fires only its owner's rules, never another owner's (leak check)."""
    a = User(username="owner_a", hashed_password="x", is_active=True)
    b = User(username="owner_b", hashed_password="x", is_active=True)
    db_session.add_all([a, b])
    db_session.flush()
    f, ex = _extraction(db_session, owner_id=a.id)   # figure owned by A
    db_session.add(AlertRule(owner_id=a.id, name="mine", enabled=True, webhook_url=None, condition={}))
    db_session.add(AlertRule(owner_id=b.id, name="theirs", enabled=True, webhook_url=None, condition={}))
    db_session.flush()
    evaluate_extraction(db_session, ex, SentNotifier())
    db_session.flush()
    alerts = db_session.execute(select(Alert).where(Alert.extraction_id == ex.id)).scalars().all()
    assert len(alerts) == 1 and alerts[0].owner_id == a.id   # B's rule excluded
