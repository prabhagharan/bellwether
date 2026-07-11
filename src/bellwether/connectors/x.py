import json
import urllib.request
from datetime import datetime, timezone
from bellwether.ssl_ctx import SSL_CONTEXT
from bellwether.connectors.base import RawItem


def _card_lines(entities: dict | None) -> list[str]:
    """Fold X url-entity link cards into extra text lines: '<title> — <description>'
    (or whichever of the two is present). URL entities with neither are skipped."""
    lines: list[str] = []
    for u in (entities or {}).get("urls", []) or []:
        title = (u.get("title") or "").strip()
        desc = (u.get("description") or "").strip()
        if title and desc:
            lines.append(f"{title} — {desc}")
        elif title or desc:
            lines.append(title or desc)
    return lines


def _parse_x_timeline(payload: dict, handle: str) -> list[RawItem]:
    items: list[RawItem] = []
    for t in payload.get("data", []):
        tid = t.get("id")
        created = t.get("created_at")
        if not tid or not created:
            continue
        published_at = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
        text = t.get("text", "")
        cards = _card_lines(t.get("entities"))
        if cards:
            text = "\n\n".join([text, *cards])
        items.append(RawItem(external_id=str(tid), text=text,
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
               f"?query=from:{self.handle}&tweet.fields=created_at,entities&max_results=20")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=10.0, context=SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []
        return _parse_x_timeline(payload, self.handle)
