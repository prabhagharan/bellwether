from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement


def list_statements(session: Session, figure_id: int | None = None,
                    status: str | None = None, limit: int = 50) -> list[Statement]:
    query = select(Statement)
    if figure_id is not None:
        query = query.where(Statement.figure_id == figure_id)
    if status is not None:
        query = query.where(Statement.status == status)
    query = query.order_by(Statement.published_at.desc()).limit(limit)
    return list(session.execute(query).scalars())
