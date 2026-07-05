from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.llm.guard import is_verbatim
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel


def split_for(statement_id: int) -> str:
    return "holdout" if statement_id % get_settings().holdout_modulus == 0 else "train"


def upsert_relevance_label(session: Session, statement_id: int, is_relevant: bool,
                           source: str = "review") -> RelevanceLabel:
    row = session.execute(
        select(RelevanceLabel).where(RelevanceLabel.statement_id == statement_id)
    ).scalar_one_or_none()
    if row is None:
        row = RelevanceLabel(statement_id=statement_id, is_relevant=is_relevant,
                             source=source, split=split_for(statement_id))
        session.add(row)
    else:
        row.is_relevant = is_relevant
        row.source = source
    session.flush()
    return row


def upsert_extraction_label(session: Session, statement_id: int, entities: list[str],
                            direction: str, magnitude: str, evidence_quote: str,
                            statement_text: str, source: str = "review") -> ExtractionLabel:
    if not is_verbatim(evidence_quote, statement_text):
        raise ValueError("gold evidence_quote must be a verbatim substring of the statement")
    row = session.execute(
        select(ExtractionLabel).where(ExtractionLabel.statement_id == statement_id)
    ).scalar_one_or_none()
    if row is None:
        row = ExtractionLabel(statement_id=statement_id, entities=entities, direction=direction,
                              magnitude=magnitude, evidence_quote=evidence_quote,
                              source=source, split=split_for(statement_id))
        session.add(row)
    else:
        row.entities = entities
        row.direction = direction
        row.magnitude = magnitude
        row.evidence_quote = evidence_quote
        row.source = source
    session.flush()
    return row
