from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.models.figure import Figure
from bellwether.models.source import Source


def create_figure(session: Session, name: str, type: str, aliases: list, owner_id: int | None,
                  discover: bool = True) -> Figure:
    discovery_status = "pending" if discover else "skipped"
    figure = Figure(name=name, type=type, aliases=aliases, owner_id=owner_id,
                    discovery_status=discovery_status)
    session.add(figure)
    session.flush()
    return figure


def list_figures(session: Session, owner_id: int | None) -> list[Figure]:
    return list(
        session.execute(select(Figure).where(Figure.owner_id == owner_id).order_by(Figure.id)).scalars()
    )


def get_figure(session: Session, figure_id: int, owner_id: int | None) -> Figure | None:
    return session.execute(
        select(Figure).where(Figure.id == figure_id, Figure.owner_id == owner_id)
    ).scalar_one_or_none()


def delete_figure(session: Session, figure_id: int, owner_id: int | None) -> bool:
    figure = get_figure(session, figure_id, owner_id)
    if figure is None:
        return False
    session.delete(figure)
    session.flush()
    return True


def add_source(session: Session, figure_id: int, connector_type: str, config: dict,
               provenance: str, origin: str, owner_id: int | None) -> Source | None:
    if get_figure(session, figure_id, owner_id) is None:
        return None
    source = Source(
        figure_id=figure_id, connector_type=connector_type, config=config,
        provenance=provenance, origin=origin, owner_id=owner_id,
    )
    session.add(source)
    session.flush()
    return source


def create_news_source(session: Session, figure: Figure, owner_id: int | None) -> Source:
    """Auto-create an enabled Google-News source for a figure (query = the figure's name).

    Deterministic, so it is enabled immediately — it does not pass through the discovery
    confidence gate that LLM-proposed sources use.
    """
    source = Source(
        figure_id=figure.id, connector_type="news", config={"query": figure.name},
        provenance="news", origin="auto", owner_id=owner_id,
        enabled=True, status="active",
        poll_interval_seconds=get_settings().news_poll_interval_seconds,
    )
    session.add(source)
    session.flush()
    return source


def backfill_news_sources(session: Session) -> int:
    """Create a news source for every figure that lacks one. Returns the count created."""
    created = 0
    for figure in session.execute(select(Figure)).scalars().all():
        has_news = session.execute(
            select(Source).where(Source.figure_id == figure.id, Source.connector_type == "news")
        ).first()
        if has_news is None:
            create_news_source(session, figure, owner_id=figure.owner_id)
            created += 1
    session.flush()
    return created


def list_sources(session: Session, figure_id: int, owner_id: int | None) -> list[Source]:
    return list(
        session.execute(
            select(Source).where(Source.figure_id == figure_id, Source.owner_id == owner_id).order_by(Source.id)
        ).scalars()
    )


def get_source(session: Session, source_id: int, owner_id: int | None) -> Source | None:
    return session.execute(
        select(Source).where(Source.id == source_id, Source.owner_id == owner_id)
    ).scalar_one_or_none()


def set_source_enabled(session: Session, source_id: int, enabled: bool, owner_id: int | None) -> Source | None:
    source = get_source(session, source_id, owner_id)
    if source is None:
        return None
    source.enabled = enabled
    session.flush()
    return source


def delete_source(session: Session, source_id: int, owner_id: int | None) -> bool:
    source = get_source(session, source_id, owner_id)
    if source is None:
        return False
    session.delete(source)
    session.flush()
    return True
