import json
import urllib.request
from datetime import datetime, timezone
from bellwether.ssl_ctx import SSL_CONTEXT
from bellwether.connectors.base import RawItem


def _parse_x_timeline(payload: dict, handle: str) -> list[RawItem]:
    items: list[RawItem] = []
    for t in payload.get("data", []):
        tid = t.get("id")
        created = t.get("created_at")
        if not tid or not created:
            continue
        published_at = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
        items.append(RawItem(external_id=str(tid), text=t.get("text", ""),
                             url=f"https://x.com/{handle}/status/{tid}", published_at=published_at))
    return items


class XConnector:
    def __init__(self, handle: str, api_key: str | None):
        self.handle = handle.lstrip("@")
        self.api_key = api_key

    def fetch(self) -> list[RawItem]:
        if not self.api_key:
            return []
        url = (f"https://api.twitter.com/2/tweets/search/recent"
               f"?query=from:{self.handle}&tweet.fields=created_at&max_results=20")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=10.0, context=SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []
        return _parse_x_timeline(payload, self.handle)
