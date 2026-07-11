import urllib.parse
from bellwether.connectors.base import RawItem
from bellwether.connectors.rss import RssConnector


class NewsConnector:
    """Fetch recent news about `query` via Google News's search-RSS feed.

    Delegates the actual fetch + parse to RssConnector, which handles SSL_CONTEXT,
    dedup by external_id, and published-date parsing.
    """

    def __init__(self, query: str, recency_days: int = 7):
        q = urllib.parse.quote(f'"{query}" when:{recency_days}d')
        self.url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

    def fetch(self) -> list[RawItem]:
        return RssConnector(self.url).fetch()
