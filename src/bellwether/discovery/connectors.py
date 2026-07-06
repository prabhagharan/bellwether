from html.parser import HTMLParser
from urllib.parse import urlparse


def youtube_feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def x_binding(handle: str) -> tuple[str, dict]:
    return "x", {"handle": handle.lstrip("@")}


def domain_of(url: str) -> str:
    netloc = urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


class _FeedLinkParser(HTMLParser):
    _FEED_TYPES = ("application/rss+xml", "application/atom+xml")

    def __init__(self):
        super().__init__()
        self.feeds: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: v for k, v in attrs}
        if a.get("rel") == "alternate" and a.get("type") in self._FEED_TYPES and a.get("href"):
            self.feeds.append(a["href"])


def discover_feed_links(html: str) -> list[str]:
    parser = _FeedLinkParser()
    parser.feed(html)
    return parser.feeds
