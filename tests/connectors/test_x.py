import json
import urllib.request
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


def test_parse_folds_link_card_title_and_description():
    payload = {"data": [{
        "id": "3", "text": "https://t.co/abc", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"title": "The White House",
                               "description": "President Trump Delivers Remarks"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "realDonaldTrump")
    assert item.text == "https://t.co/abc\n\nThe White House — President Trump Delivers Remarks"


def test_parse_card_with_only_one_of_title_or_description():
    payload = {"data": [{
        "id": "4", "text": "vid", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"description": "just a description"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "vid\n\njust a description"


def test_parse_skips_url_entity_without_title_or_description():
    payload = {"data": [{
        "id": "5", "text": "plain https://t.co/x", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"expanded_url": "https://example.com"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "plain https://t.co/x"


def test_parse_folds_multiple_cards():
    payload = {"data": [{
        "id": "6", "text": "two links", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"title": "A", "description": "aa"},
                              {"title": "B", "description": "bb"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "two links\n\nA — aa\n\nB — bb"


def test_fetch_requests_entities_and_folds_cards(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"data": [{
                "id": "9", "text": "https://t.co/z", "created_at": "2026-07-05T03:00:00.000Z",
                "entities": {"urls": [{"title": "T", "description": "D"}]}}]}).encode()

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    items = XConnector("h", api_key="k").fetch()
    assert "tweet.fields=created_at,entities" in captured["url"]
    assert items[0].text == "https://t.co/z\n\nT — D"
