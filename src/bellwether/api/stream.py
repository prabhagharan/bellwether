import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session, SessionLocal
from bellwether.config import get_settings
from bellwether.models.alert import Alert
from bellwether.models.user import User
# Reuse the SAME token decode as get_current_user (see security/deps.py).
from bellwether.security.deps import resolve_token

router = APIRouter()


def fetch_new_alerts(session: Session, owner_id, after_id: int, limit: int = 50) -> list[Alert]:
    return list(session.execute(
        select(Alert).where(Alert.owner_id == owner_id, Alert.id > after_id)
        .order_by(Alert.id).limit(limit)
    ).scalars())


def _user_from_token(token: str, session: Session) -> User:
    user = resolve_token(token, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


@router.get("/stream")
def stream(token: str = Query(...), session: Session = Depends(get_session)):
    user = _user_from_token(token, session)
    owner_id = user.id
    poll = get_settings().sse_poll_interval_seconds

    async def gen():
        with SessionLocal() as s:
            last_id = s.execute(
                select(Alert.id).where(Alert.owner_id == owner_id).order_by(Alert.id.desc()).limit(1)
            ).scalar() or 0
        ticks = 0
        while True:
            with SessionLocal() as s:
                new = fetch_new_alerts(s, owner_id, last_id)
            for a in new:
                last_id = a.id
                yield f"event: alert\ndata: {json.dumps(a.payload)}\n\n"
            ticks += 1
            if ticks % 8 == 0:
                yield ": ping\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(gen(), media_type="text/event-stream")
