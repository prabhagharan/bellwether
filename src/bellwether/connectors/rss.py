import calendar
from datetime import datetime, timezone
import feedparser
from bellwether.connectors.base import RawItem


class RssConnector:
    def __init__(self, feed_url: str):
        self.feed_url = feed_url

    def fetch(self) -> list[RawItem]:
        parsed = feedparser.parse(self.feed_url)
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
