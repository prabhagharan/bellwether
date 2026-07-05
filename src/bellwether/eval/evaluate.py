from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.eval_run import EvalRun
from bellwether.eval.metrics import score_detection, score_extraction, GoldExtraction

# Firewall: this module imports Statement + labels + metrics only — never Impact/Resolution.


@dataclass(frozen=True)
class EvalResult:
    score: float
    n: int


def evaluate_detect(session: Session, detector, split: str, dspy_program_id: int | None = None) -> EvalResult:
    rows = session.execute(
        select(Statement, RelevanceLabel)
        .join(RelevanceLabel, RelevanceLabel.statement_id == Statement.id)
        .where(RelevanceLabel.split == split)
    ).all()
    if rows:
        total = 0.0
        for st, lab in rows:
            pred = detector.detect(st.text)
            s, _ = score_detection(pred.is_relevant, lab.is_relevant)
            total += s
        result = EvalResult(total / len(rows), len(rows))
    else:
        result = EvalResult(0.0, 0)
    session.add(EvalRun(module="detect", dspy_program_id=dspy_program_id, split=split,
                        metric="accuracy", score=result.score, n=result.n))
    session.flush()
    return result


def evaluate_extract(session: Session, extractor, split: str, dspy_program_id: int | None = None) -> EvalResult:
    rows = session.execute(
        select(Statement, ExtractionLabel)
        .join(ExtractionLabel, ExtractionLabel.statement_id == Statement.id)
        .where(ExtractionLabel.split == split)
    ).all()
    if rows:
        total = 0.0
        for st, lab in rows:
            pred = extractor.extract(st.text)
            gold = GoldExtraction(entities=lab.entities, direction=lab.direction,
                                  magnitude=lab.magnitude, evidence_quote=lab.evidence_quote)
            s, _ = score_extraction(pred, gold, st.text)
            total += s
        result = EvalResult(total / len(rows), len(rows))
    else:
        result = EvalResult(0.0, 0)
    session.add(EvalRun(module="extract", dspy_program_id=dspy_program_id, split=split,
                        metric="extract_avg", score=result.score, n=result.n))
    session.flush()
    return result
