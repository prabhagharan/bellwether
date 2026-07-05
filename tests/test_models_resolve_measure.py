from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact


def test_resolution_columns():
    cols = set(Resolution.__table__.columns.keys())
    assert {"id", "extraction_id", "entity", "symbol", "asset_class", "measurable", "created_at"} <= cols
    assert "owner_id" not in cols


def test_entity_symbol_columns_and_unique():
    cols = set(EntitySymbol.__table__.columns.keys())
    assert {"id", "normalized_entity", "symbol", "asset_class", "measurable",
            "instrument_name", "confidence", "source", "created_at"} <= cols
    assert "owner_id" not in cols
    assert EntitySymbol.__table__.columns["normalized_entity"].unique is True


def test_impact_columns_and_unique():
    cols = set(Impact.__table__.columns.keys())
    assert {"id", "resolution_id", "symbol", "asset_class", "window", "event_at", "due_at",
            "status", "price_t0", "price_after", "pct_move", "volume_spike",
            "measured_at", "claimed_at", "created_at"} <= cols
    assert "owner_id" not in cols
    uniques = [c for c in Impact.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"resolution_id", "window"} for u in uniques)
