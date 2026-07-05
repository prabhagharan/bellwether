# src/bellwether/worker.py
from dataclasses import dataclass
from typing import Callable
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.llm.contracts import Detector, Extractor
from bellwether.llm.guard import is_verbatim


@dataclass
class Stage:
    name: str
    claim_from: str
    claim_to: str
    process: Callable[[Session, Statement], None]


def make_detect_stage(detector: Detector, threshold: float) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        result = detector.detect(statement.text)
        session.add(Detection(
            statement_id=statement.id,
            is_relevant=result.is_relevant,
            score=result.score,
            model=detector.model,
            version="baseline",
        ))
        statement.status = "detected" if (result.is_relevant and result.score >= threshold) else "irrelevant"
        statement.claimed_at = None
        session.commit()

    return Stage(name="detect", claim_from="new", claim_to="detecting", process=process)


def make_extract_stage(extractor: Extractor) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        try:
            result = extractor.extract(statement.text)
        except Exception:
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        # Verbatim-substring guard at the stage boundary — outside the module, so no
        # extractor implementation can ever land a fabricated quote.
        if not is_verbatim(result.evidence_quote, statement.text):
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        session.add(Extraction(
            statement_id=statement.id,
            entities=result.entities,
            direction=result.direction,
            magnitude=result.magnitude,
            confidence=result.confidence,
            evidence_quote=result.evidence_quote,
            model=extractor.model,
            version="baseline",
        ))
        statement.status = "extracted"
        statement.claimed_at = None
        session.commit()

    return Stage(name="extract", claim_from="detected", claim_to="extracting", process=process)
