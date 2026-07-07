from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.alert_rule import AlertRule
from bellwether.api.schemas import AlertRuleCreate, AlertRuleUpdate, AlertRuleRead

router = APIRouter()


@router.post("/alert_rules", response_model=AlertRuleRead, status_code=status.HTTP_201_CREATED)
def create_rule(body: AlertRuleCreate, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = AlertRule(owner_id=user.id, name=body.name,
                     condition=body.condition.model_dump(exclude_none=True),
                     webhook_url=body.webhook_url, enabled=body.enabled)
    session.add(rule)
    session.flush()
    return rule


@router.get("/alert_rules", response_model=list[AlertRuleRead])
def list_rules(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return list(session.execute(
        select(AlertRule).where(AlertRule.owner_id == user.id).order_by(AlertRule.id.desc())
    ).scalars())


def _owned(session, rule_id, user):
    return session.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.owner_id == user.id)
    ).scalar_one_or_none()


@router.patch("/alert_rules/{rule_id}", response_model=AlertRuleRead)
def update_rule(rule_id: int, body: AlertRuleUpdate, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = _owned(session, rule_id, user)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if body.name is not None:
        rule.name = body.name
    if body.condition is not None:
        rule.condition = body.condition.model_dump(exclude_none=True)
    if body.webhook_url is not None:
        rule.webhook_url = body.webhook_url
    if body.enabled is not None:
        rule.enabled = body.enabled
    session.flush()
    return rule


@router.delete("/alert_rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    rule = _owned(session, rule_id, user)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.flush()
