# src/bellwether/queue.py
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, func, or_
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.impact import Impact
from bellwether.models.figure import Figure
from bellwether.models.extraction import Extraction
from bellwether.models.source import Source


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


def claim_pending_figure(session: Session, to_status: str = "running") -> Figure | None:
    figure = session.execute(
        select(Figure).where(Figure.discovery_status == "pending")
        .order_by(Figure.id).with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if figure is None:
        return None
    figure.discovery_status = to_status
    figure.discovery_claimed_at = datetime.now(timezone.utc)
    session.commit()
    return figure


def reclaim_stale_figures(session: Session, in_status: str, to_status: str,
                          older_than_seconds: float) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Figure).where(Figure.discovery_status == in_status, Figure.discovery_claimed_at < cutoff)
        .values(discovery_status=to_status, discovery_claimed_at=None)
    )
    session.commit()
    return result.rowcount


def claim_pending_extraction(session: Session, to_status: str = "alerting") -> Extraction | None:
    extraction = session.execute(
        select(Extraction).where(Extraction.alert_status == "pending")
        .order_by(Extraction.id).with_for_update(skip_locked=True).limit(1)
    ).scalar_one_or_none()
    if extraction is None:
        return None
    extraction.alert_status = to_status
    extraction.alert_claimed_at = datetime.now(timezone.utc)
    session.commit()
    return extraction


def reclaim_stale_alerting(session: Session, in_status: str, to_status: str,
                           older_than_seconds: float) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Extraction).where(Extraction.alert_status == in_status, Extraction.alert_claimed_at < cutoff)
        .values(alert_status=to_status, alert_claimed_at=None)
    )
    session.commit()
    return result.rowcount


def claim_due_source(session: Session) -> Source | None:
    """Claim one enabled source whose poll interval has elapsed, stamp-first.

    Mirrors claim_due_impact, but the schedule clock IS the claim guard: we stamp
    last_polled_at = now() and commit BEFORE the caller fetches. A source is "due"
    when it has never been polled, or last_polled_at + poll_interval_seconds <= now.
    Because the timer is advanced at claim time, a failed fetch simply backs off a
    full interval, and a crash mid-fetch strands nothing (no in-flight status), so
    there is no reclaim for this stage.
    """
    now = datetime.now(timezone.utc)
    due_at = Source.last_polled_at + func.make_interval(0, 0, 0, 0, 0, 0, Source.poll_interval_seconds)
    source = session.execute(
        select(Source)
        .where(
            Source.enabled.is_(True),
            or_(Source.last_polled_at.is_(None), due_at <= now),
        )
        .order_by(Source.last_polled_at.asc().nullsfirst())
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if source is None:
        return None
    source.last_polled_at = now
    session.commit()
    return source
