from bellwether.models.figure import Figure
from bellwether.models.source import Source


def test_figure_discovery_columns():
    c = set(Figure.__table__.columns.keys())
    assert {"wikidata_id", "discovery_status", "discovery_claimed_at", "discovery_error"} <= c


def test_source_discovery_columns():
    c = set(Source.__table__.columns.keys())
    assert {"status", "verified", "discovery_confidence", "discovery_meta"} <= c
    assert Source.__table__.columns["status"].default.arg == "active"
    assert Source.__table__.columns["verified"].default.arg is False
