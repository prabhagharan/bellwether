from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.api.schemas import DiscoveryQueueItem, DiscoveryDecision

router = APIRouter()


@router.get("/discovery/queue", response_model=list[DiscoveryQueueItem])
def discovery_queue(limit: int = Query(default=50, ge=1, le=500),
                    session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    rows = session.execute(
        select(Source, Figure).join(Figure, Figure.id == Source.figure_id)
        .where(Figure.owner_id == user.id, Source.status == "pending_review")
        .order_by(Source.id).limit(limit)
    ).all()
    return [DiscoveryQueueItem(
        source_id=s.id, figure_id=f.id, figure_name=f.name, connector_type=s.connector_type,
        config=s.config, discovery_confidence=s.discovery_confidence, discovery_meta=s.discovery_meta,
    ) for s, f in rows]


@router.post("/discovery/{source_id}")
def review_source(source_id: int, body: DiscoveryDecision,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if body.decision not in ("confirm", "reject"):
        raise HTTPException(status_code=422, detail="decision must be 'confirm' or 'reject'")
    source = session.execute(
        select(Source).join(Figure, Figure.id == Source.figure_id)
        .where(Source.id == source_id, Figure.owner_id == user.id)
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.decision == "confirm":
        source.status, source.enabled, source.verified = "active", True, True
    else:
        source.status, source.enabled = "rejected", False
    session.flush()
    return {"ok": True}
