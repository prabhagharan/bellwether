import dspy
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel


def build_trainset(session: Session, module: str, split: str = "train") -> list[dspy.Example]:
    if module == "detect":
        rows = session.execute(
            select(Statement, RelevanceLabel)
            .join(RelevanceLabel, RelevanceLabel.statement_id == Statement.id)
            .where(RelevanceLabel.split == split)
        ).all()
        return [dspy.Example(statement_text=st.text, is_relevant=lab.is_relevant)
                .with_inputs("statement_text") for st, lab in rows]
    rows = session.execute(
        select(Statement, ExtractionLabel)
        .join(ExtractionLabel, ExtractionLabel.statement_id == Statement.id)
        .where(ExtractionLabel.split == split)
    ).all()
    return [dspy.Example(statement_text=st.text, entities=lab.entities, direction=lab.direction,
                         magnitude=lab.magnitude, evidence_quote=lab.evidence_quote)
            .with_inputs("statement_text") for st, lab in rows]
