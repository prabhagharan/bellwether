# src/bellwether/queue.py
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement


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
