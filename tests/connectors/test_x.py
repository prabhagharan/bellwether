from bellwether.connectors.x import _parse_x_timeline, XConnector


def test_parse_x_timeline():
    payload = {"data": [
        {"id": "1", "text": "hello", "created_at": "2026-07-01T12:00:00.000Z"},
        {"id": "2", "text": "world", "created_at": "2026-07-02T09:30:00.000Z"},
    ]}
    items = _parse_x_timeline(payload, "jack")
    assert [i.external_id for i in items] == ["1", "2"]
    assert items[0].text == "hello" and items[0].url == "https://x.com/jack/status/1"
    assert items[0].published_at.year == 2026


def test_connector_disabled_without_key():
    assert XConnector("jack", api_key=None).fetch() == []
