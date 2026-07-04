from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.connectors.registry import build_connector, UnknownConnectorType
from bellwether.models.source import Source
from bellwether.models.statement import Statement


def ingest_source(session: Session, source: Source) -> list[Statement]:
    connector = build_connector(source)
    items = connector.fetch()
    existing = set(
        session.execute(
            select(Statement.external_id).where(Statement.source_id == source.id)
        ).scalars()
    )
    new_statements: list[Statement] = []
    for item in items:
        if item.external_id in existing:
            continue
        statement = Statement(
            figure_id=source.figure_id,
            source_id=source.id,
            external_id=item.external_id,
            text=item.text,
            url=item.url,
            provenance=source.provenance,
            published_at=item.published_at,
            status="new",
        )
        session.add(statement)
        new_statements.append(statement)
        existing.add(item.external_id)
    source.last_polled_at = datetime.now(timezone.utc)
    session.flush()
    return new_statements


def run_ingest_pass(session: Session) -> int:
    sources = session.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    total = 0
    for source in sources:
        try:
            total += len(ingest_source(session, source))
        except UnknownConnectorType:
            continue
    session.commit()
    return total
