import urllib.parse
from bellwether.connectors.news import NewsConnector
from bellwether.connectors.registry import build_connector, KNOWN_CONNECTOR_TYPES
from bellwether.models.source import Source
from bellwether.config import Settings


def test_news_url_construction():
    conn = NewsConnector("Jerome Powell", recency_days=7)
    assert conn.url == (
        "https://news.google.com/rss/search?"
        "q=%22Jerome%20Powell%22%20when%3A7d&hl=en-US&gl=US&ceid=US:en"
    )


def test_news_fetch_delegates_to_rss(monkeypatch):
    captured = {}

    class StubRss:
        def __init__(self, url):
            captured["url"] = url

        def fetch(self):
            return ["a", "b"]

    monkeypatch.setattr("bellwether.connectors.news.RssConnector", StubRss)
    conn = NewsConnector("Powell", recency_days=7)
    assert conn.fetch() == ["a", "b"]
    assert captured["url"] == conn.url


def test_build_connector_returns_news_connector():
    src = Source(connector_type="news", config={"query": "Powell"})
    conn = build_connector(src)
    assert isinstance(conn, NewsConnector)
    assert "Powell" in urllib.parse.unquote(conn.url)


def test_news_in_known_connector_types():
    assert "news" in KNOWN_CONNECTOR_TYPES


def test_news_settings_defaults():
    s = Settings(database_url="postgresql+psycopg://x/y", jwt_secret="s",
                 admin_username="a", admin_password="b")
    assert s.news_recency_days == 7
    assert s.news_poll_interval_seconds == 1800
