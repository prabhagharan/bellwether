# tests/test_models_alerts.py
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.models.extraction import Extraction


def test_alert_rule_columns():
    c = set(AlertRule.__table__.columns.keys())
    assert {"id", "owner_id", "name", "condition", "webhook_url", "enabled", "created_at"} <= c


def test_alert_columns_and_unique():
    c = set(Alert.__table__.columns.keys())
    assert {"id", "extraction_id", "rule_id", "owner_id", "payload", "webhook_status", "sent_at", "created_at"} <= c
    uniques = [u for u in Alert.__table__.constraints if u.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"extraction_id", "rule_id"} for u in uniques)


def test_extraction_alert_columns():
    c = set(Extraction.__table__.columns.keys())
    assert {"alert_status", "alert_claimed_at"} <= c
    assert Extraction.__table__.columns["alert_status"].default.arg == "pending"
