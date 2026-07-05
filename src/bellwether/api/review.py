from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.figure import Figure
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.labels import upsert_relevance_label, upsert_extraction_label
from bellwether.api.schemas import ReviewSubmit, ReviewQueueItem

router = APIRouter()


@router.get("/review/queue", response_model=list[ReviewQueueItem])
def review_queue(module: str = Query(pattern="^(extract|detect)$"),
                 limit: int = Query(default=50, ge=1, le=500),
                 session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    labelled = ExtractionLabel if module == "extract" else RelevanceLabel
    q = (select(Statement, Figure)
         .join(Figure, Figure.id == Statement.figure_id)
         .outerjoin(labelled, labelled.statement_id == Statement.id)
         .where(Figure.owner_id == user.id, labelled.id.is_(None)))
    if module == "extract":
        q = q.where(Statement.status.in_(("extracted", "resolved")))
    q = q.order_by(Statement.published_at.desc()).limit(limit)
    items = []
    for st, fig in session.execute(q).all():
        ex = session.execute(select(Extraction).where(Extraction.statement_id == st.id)).scalar_one_or_none()
        current = None if ex is None else {
            "entities": ex.entities, "direction": ex.direction, "magnitude": ex.magnitude,
            "confidence": ex.confidence, "evidence_quote": ex.evidence_quote,
        }
        items.append(ReviewQueueItem(statement_id=st.id, text=st.text, figure_name=fig.name,
                                     current_extraction=current))
    return items


@router.post("/review/{statement_id}")
def submit_review(statement_id: int, body: ReviewSubmit,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    st = session.execute(
        select(Statement).join(Figure, Figure.id == Statement.figure_id)
        .where(Statement.id == statement_id, Figure.owner_id == user.id)
    ).scalar_one_or_none()
    if st is None:
        raise HTTPException(status_code=404, detail="Statement not found")

    upsert_relevance_label(session, statement_id, body.is_relevant)
    if body.is_relevant:
        if body.extraction is not None:
            g = body.extraction
            entities, direction, magnitude, quote = g.entities, g.direction, g.magnitude, g.evidence_quote
        else:  # confirm: copy the model's current extraction as gold
            ex = session.execute(select(Extraction).where(Extraction.statement_id == statement_id)).scalar_one_or_none()
            if ex is None:
                raise HTTPException(status_code=422, detail="no extraction to confirm; provide one")
            entities, direction, magnitude, quote = ex.entities, ex.direction, ex.magnitude, ex.evidence_quote
        try:
            upsert_extraction_label(session, statement_id, entities, direction, magnitude, quote, st.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}
