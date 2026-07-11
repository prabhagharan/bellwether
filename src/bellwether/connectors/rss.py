import calendar
import urllib.request
from datetime import datetime, timezone
import feedparser
from bellwether.connectors.base import RawItem
from bellwether.ssl_ctx import SSL_CONTEXT


class RssConnector:
    def __init__(self, feed_url: str):
        self.feed_url = feed_url

    def fetch(self) -> list[RawItem]:
        # feedparser's built-in fetch does not use the project's SSL_CONTEXT, so https
        # feeds fail certificate verification in restricted environments and silently
        # return zero entries. Fetch the bytes ourselves with SSL_CONTEXT (like the
        # other connectors) and hand them to feedparser. Non-http sources (local file
        # paths used by the test suite) go straight to feedparser unchanged.
        if self.feed_url.startswith(("http://", "https://")):
            req = urllib.request.Request(self.feed_url, headers={"User-Agent": "bellwether/1.0"})
            with urllib.request.urlopen(req, timeout=10.0, context=SSL_CONTEXT) as resp:
                source: str | bytes = resp.read()
        else:
            source = self.feed_url
        parsed = feedparser.parse(source)
        items: list[RawItem] = []
        for entry in parsed.entries:
            external_id = entry.get("id") or entry.get("guid") or entry.get("link")
            tstruct = entry.get("published_parsed") or entry.get("updated_parsed")
            if not external_id or not tstruct:
                continue
            published_at = datetime.fromtimestamp(calendar.timegm(tstruct), tz=timezone.utc)
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title}\n\n{summary}" if summary else title
            items.append(
                RawItem(
                    external_id=str(external_id),
                    text=text,
                    url=entry.get("link"),
                    published_at=published_at,
                )
            )
        return items
