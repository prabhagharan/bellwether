from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.source import Source
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.trackb.report import leaderboard_by_figure
from bellwether.api.schemas import LeaderboardRow, SignalRead, ImpactRead

router = APIRouter()


@router.get("/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return leaderboard_by_figure(session, user.id)


@router.get("/signals", response_model=list[SignalRead])
def signals(figure_id: int | None = None, direction: str | None = None,
            min_confidence: float | None = None, limit: int = Query(default=50, ge=1, le=500),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    q = (select(Extraction, Statement.text, Statement.url, Statement.published_at,
                Source.connector_type, Figure.name)
         .join(Statement, Statement.id == Extraction.statement_id)
         .join(Source, Source.id == Statement.source_id)
         .join(Figure, Figure.id == Statement.figure_id)
         .where(Figure.owner_id == user.id))
    if figure_id is not None:
        q = q.where(Statement.figure_id == figure_id)
    if direction is not None:
        q = q.where(Extraction.direction == direction)
    if min_confidence is not None:
        q = q.where(Extraction.confidence >= min_confidence)
    q = q.order_by(Extraction.id.desc()).limit(limit)
    return [
        SignalRead(
            id=ex.id, statement_id=ex.statement_id, direction=ex.direction,
            magnitude=ex.magnitude, confidence=ex.confidence, entities=ex.entities,
            version=ex.version, text=text, url=url, source_type=connector_type,
            figure_name=figure_name, published_at=published_at, evidence_quote=ex.evidence_quote,
        )
        for ex, text, url, published_at, connector_type, figure_name in session.execute(q).all()
    ]


@router.get("/impacts", response_model=list[ImpactRead])
def impacts(figure_id: int | None = None, symbol: str | None = None, window: str | None = None,
            limit: int = Query(default=50, ge=1, le=500),
            session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    q = (select(Impact).join(Resolution, Resolution.id == Impact.resolution_id)
         .join(Extraction, Extraction.id == Resolution.extraction_id)
         .join(Statement, Statement.id == Extraction.statement_id)
         .join(Figure, Figure.id == Statement.figure_id).where(Figure.owner_id == user.id))
    if figure_id is not None:
        q = q.where(Statement.figure_id == figure_id)
    if symbol is not None:
        q = q.where(Impact.symbol == symbol)
    if window is not None:
        q = q.where(Impact.window == window)
    q = q.order_by(Impact.id.desc()).limit(limit)
    return list(session.execute(q).scalars())
