from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.repositories.statements import list_statements
from bellwether.api.schemas import StatementRead

router = APIRouter()


@router.get("/statements", response_model=list[StatementRead])
def get_statements(
    figure_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return list_statements(session, figure_id=figure_id, status=status, limit=limit)
