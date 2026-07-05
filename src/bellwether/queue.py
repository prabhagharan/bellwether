# src/bellwether/queue.py
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.impact import Impact


def claim_one(session: Session, from_status: str, to_status: str) -> Statement | None:
    """Claim the oldest statement in `from_status` and flip it to `to_status`.

    Uses FOR UPDATE SKIP LOCKED so concurrent workers never claim the same row. The
    short claim transaction commits immediately, releasing the row lock BEFORE the slow
    LLM work runs — a worker never holds a row lock across network I/O.
    """
    statement = session.execute(
        select(Statement)
        .where(Statement.status == from_status)
        .order_by(Statement.published_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if statement is None:
        return None
    statement.status = to_status
    statement.claimed_at = datetime.now(timezone.utc)
    session.commit()
    return statement


def reclaim_stale(session: Session, in_status: str, to_status: str,
                  older_than_seconds: float) -> int:
    """Reset rows stuck in an in-flight status past the cutoff back to `to_status`.

    Recovery for workers that crashed mid-process, leaving rows in `detecting` /
    `extracting`. Returns the number of rows reset.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Statement)
        .where(Statement.status == in_status, Statement.claimed_at < cutoff)
        .values(status=to_status, claimed_at=None)
    )
    session.commit()
    return result.rowcount


def claim_due_impact(session: Session, to_status: str = "measuring") -> Impact | None:
    """Claim the oldest-due `pending` impact whose window has elapsed.

    Same lock-then-release discipline as claim_one, with an added `due_at <= now()`
    filter so future windows aren't claimed early.
    """
    now = datetime.now(timezone.utc)
    impact = session.execute(
        select(Impact)
        .where(Impact.status == "pending", Impact.due_at <= now)
        .order_by(Impact.due_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if impact is None:
        return None
    impact.status = to_status
    impact.claimed_at = now
    session.commit()
    return impact


def reclaim_stale_impacts(session: Session, in_status: str, to_status: str,
                          older_than_seconds: float) -> int:
    """Reset impacts stuck in an in-flight status past the cutoff (crash recovery)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Impact)
        .where(Impact.status == in_status, Impact.claimed_at < cutoff)
        .values(status=to_status, claimed_at=None)
    )
    session.commit()
    return result.rowcount
