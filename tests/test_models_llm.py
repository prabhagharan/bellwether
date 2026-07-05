from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.models.statement import Statement


def test_detection_columns():
    cols = set(Detection.__table__.columns.keys())
    assert {"id", "statement_id", "is_relevant", "score", "model", "version", "created_at"} <= cols
    assert "owner_id" not in cols  # shared corpus


def test_extraction_columns():
    cols = set(Extraction.__table__.columns.keys())
    assert {"id", "statement_id", "entities", "direction", "magnitude",
            "confidence", "evidence_quote", "model", "version", "created_at"} <= cols
    assert "owner_id" not in cols  # shared corpus


def test_statement_has_claimed_at():
    assert "claimed_at" in Statement.__table__.columns.keys()
