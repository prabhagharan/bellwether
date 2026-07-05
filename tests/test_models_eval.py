from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun


def test_label_columns_and_unique():
    rc = set(RelevanceLabel.__table__.columns.keys())
    assert {"id", "statement_id", "is_relevant", "source", "split", "created_at"} <= rc
    assert "owner_id" not in rc
    assert RelevanceLabel.__table__.columns["statement_id"].unique is True
    ec = set(ExtractionLabel.__table__.columns.keys())
    assert {"id", "statement_id", "entities", "direction", "magnitude",
            "evidence_quote", "source", "split", "created_at"} <= ec
    assert ExtractionLabel.__table__.columns["statement_id"].unique is True


def test_program_and_evalrun_columns():
    pc = set(DspyProgram.__table__.columns.keys())
    assert {"id", "module", "version", "artifact", "holdout_score", "is_champion", "created_at"} <= pc
    assert "owner_id" not in pc
    uniques = [c for c in DspyProgram.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"module", "version"} for u in uniques)
    er = set(EvalRun.__table__.columns.keys())
    assert {"id", "module", "dspy_program_id", "split", "metric", "score", "n", "created_at"} <= er
