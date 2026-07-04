from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement

def test_ingestion_models_columns():
    assert {"id", "name", "type", "aliases", "owner_id", "created_at"} <= set(Figure.__table__.columns.keys())
    assert {"id", "figure_id", "connector_type", "config", "provenance", "origin",
            "enabled", "poll_interval_seconds", "last_polled_at", "owner_id", "created_at"} <= set(Source.__table__.columns.keys())
    stmt_cols = set(Statement.__table__.columns.keys())
    assert {"id", "figure_id", "source_id", "external_id", "text", "url",
            "provenance", "published_at", "ingested_at", "status"} <= stmt_cols
    assert "owner_id" not in stmt_cols  # statements are shared corpus
    # dedup uniqueness on (source_id, external_id)
    uniques = [c for c in Statement.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"source_id", "external_id"} for u in uniques)
