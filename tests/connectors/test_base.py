from datetime import datetime, timezone
from bellwether.connectors.base import RawItem, SourceConnector

def test_rawitem_holds_fields():
    ts = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    item = RawItem(external_id="abc", text="hello", url="https://x/y", published_at=ts)
    assert item.external_id == "abc"
    assert item.text == "hello"
    assert item.url == "https://x/y"
    assert item.published_at == ts

def test_connector_protocol_is_runtime_checkable():
    class Dummy:
        def fetch(self):
            return []
    assert isinstance(Dummy(), SourceConnector)
