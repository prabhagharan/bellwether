from datetime import datetime, timezone
from pathlib import Path
from bellwether.connectors.rss import RssConnector

FEED = str(Path(__file__).parent.parent / "fixtures" / "sample_feed.xml")

def test_rss_fetch_maps_entries():
    items = RssConnector(FEED).fetch()
    assert len(items) == 2
    first = next(i for i in items if i.external_id == "speech-0001")
    assert "Rates will stay higher for longer" in first.text
    assert "restrictive stance" in first.text
    assert first.url == "https://example.test/speech-1"
    assert first.published_at == datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc)

def test_rss_published_at_is_utc_not_local():
    # 14:30 GMT must parse to exactly 14:30 UTC regardless of the machine's local tz
    first = next(i for i in RssConnector(FEED).fetch() if i.external_id == "speech-0001")
    assert first.published_at.tzinfo is not None
    assert first.published_at.hour == 14 and first.published_at.minute == 30
