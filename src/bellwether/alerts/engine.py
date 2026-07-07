from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.figure import Figure
from bellwether.models.alert_rule import AlertRule
from bellwether.models.alert import Alert
from bellwether.alerts.rules import matches


def _payload(figure: Figure, statement: Statement, extraction) -> dict:
    text = (f"{figure.name}: {extraction.direction}/{extraction.magnitude} "
            f"(confidence {extraction.confidence:.2f}) — {statement.text[:160]}")
    return {
        "figure": figure.name, "figure_id": figure.id,
        "direction": extraction.direction, "magnitude": extraction.magnitude,
        "confidence": extraction.confidence, "entities": list(extraction.entities),
        "url": statement.url, "text": text,
    }


def evaluate_extraction(session: Session, extraction, notifier) -> None:
    statement = session.get(Statement, extraction.statement_id)
    figure = session.get(Figure, statement.figure_id)
    owner_id = figure.owner_id
    rules = session.execute(
        select(AlertRule).where(AlertRule.enabled.is_(True), AlertRule.owner_id.is_(owner_id)
                                if owner_id is None else AlertRule.owner_id == owner_id)
    ).scalars().all()
    for rule in rules:
        if not matches(rule.condition, extraction, figure.id):
            continue
        exists = session.execute(
            select(Alert).where(Alert.extraction_id == extraction.id, Alert.rule_id == rule.id)
        ).scalar_one_or_none()
        if exists is not None:
            continue
        payload = _payload(figure, statement, extraction)
        alert = Alert(extraction_id=extraction.id, rule_id=rule.id, owner_id=owner_id, payload=payload,
                      webhook_status="pending" if rule.webhook_url else "skipped")
        session.add(alert)
        session.flush()
        if rule.webhook_url:
            outcome = notifier.notify(rule.webhook_url, payload)
            alert.webhook_status = "sent" if outcome.ok else "failed"
            alert.sent_at = datetime.now(timezone.utc) if outcome.ok else None
