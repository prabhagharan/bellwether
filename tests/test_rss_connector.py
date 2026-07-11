from pathlib import Path
import urllib.request
from bellwether.connectors.rss import RssConnector
from bellwether.ssl_ctx import SSL_CONTEXT

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_rss_https_fetch_uses_ssl_context(monkeypatch):
    """https feeds must be fetched via urllib with the project SSL_CONTEXT, not via
    feedparser's own SSL-blind fetch (which fails cert verification in restricted
    environments and silently yields zero entries). Serve the fixture bytes through a
    fake urlopen and assert the feed parses AND our SSL_CONTEXT was passed through."""
    feed_bytes = FIXTURE.read_bytes()
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return feed_bytes

    def fake_urlopen(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["context"] = context
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    items = RssConnector("https://feeds.example.invalid/rss.xml").fetch()
    assert len(items) == 2                     # parsed from the fetched bytes
    assert seen["context"] is SSL_CONTEXT      # used the project's SSL context
    assert seen["url"] == "https://feeds.example.invalid/rss.xml"


def test_rss_local_path_still_parses():
    """Non-http feed sources (local file paths, used across the test suite) still parse
    straight through feedparser."""
    items = RssConnector(str(FIXTURE)).fetch()
    assert len(items) == 2
