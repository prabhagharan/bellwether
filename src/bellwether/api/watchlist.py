from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.repositories import watchlist as repo
from bellwether.api.schemas import FigureCreate, FigureRead, SourceCreate, SourceRead, SourceUpdate

router = APIRouter()


@router.post("/figures", response_model=FigureRead, status_code=status.HTTP_201_CREATED)
def create_figure(body: FigureCreate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return repo.create_figure(session, body.name, body.type, body.aliases, owner_id=user.id)


@router.get("/figures", response_model=list[FigureRead])
def list_figures(session: Session = Depends(get_session), user: User = Depends(get_current_user)):
    return repo.list_figures(session, owner_id=user.id)


@router.delete("/figures/{figure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_figure(figure_id: int, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if not repo.delete_figure(session, figure_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="Figure not found")


@router.post("/figures/{figure_id}/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def add_source(figure_id: int, body: SourceCreate, session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    if repo.get_figure(session, figure_id, owner_id=user.id) is None:
        raise HTTPException(status_code=404, detail="Figure not found")
    if body.connector_type == "rss" and "feed_url" not in body.config:
        raise HTTPException(status_code=422, detail="rss source requires config.feed_url")
    source = repo.add_source(session, figure_id, body.connector_type, body.config,
                             body.provenance, "manual", owner_id=user.id)
    return source


@router.get("/figures/{figure_id}/sources", response_model=list[SourceRead])
def list_sources(figure_id: int, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    if repo.get_figure(session, figure_id, owner_id=user.id) is None:
        raise HTTPException(status_code=404, detail="Figure not found")
    return repo.list_sources(session, figure_id, owner_id=user.id)


@router.patch("/sources/{source_id}", response_model=SourceRead)
def update_source(source_id: int, body: SourceUpdate, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    source = repo.set_source_enabled(session, source_id, body.enabled, owner_id=user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    if not repo.delete_source(session, source_id, owner_id=user.id):
        raise HTTPException(status_code=404, detail="Source not found")
