import json
import os
import urllib.request
from bellwether.discovery.contracts import SearchResult, WebSearch, DiscoveryError

_ENDPOINT = "https://api.tavily.com/search"


def _parse_tavily(payload: dict) -> list[SearchResult]:
    return [
        SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
        for r in payload.get("results", [])
    ]


class TavilyAdapter:
    def __init__(self, api_key: str | None, timeout: float = 10.0):
        self._api_key = api_key
        self._timeout = timeout

    def search(self, query: str) -> list[SearchResult]:
        if not self._api_key:
            return []
        body = json.dumps({"api_key": self._api_key, "query": query, "max_results": 5}).encode()
        req = urllib.request.Request(_ENDPOINT, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise DiscoveryError("tavily request failed") from exc
        return _parse_tavily(payload)


def build_web_search() -> WebSearch:
    return TavilyAdapter(os.environ.get("TAVILY_API_KEY"))
