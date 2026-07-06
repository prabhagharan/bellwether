# src/bellwether/discovery/http.py
import urllib.request
from bellwether.ssl_ctx import SSL_CONTEXT
from bellwether.discovery.contracts import FetchResult, HttpClient


class UrllibHttpClient:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def get(self, url: str) -> FetchResult:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bellwether/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout, context=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    return FetchResult(ok=False, text=None)
                raw = resp.read()
                return FetchResult(ok=True, text=raw.decode("utf-8", errors="replace"))
        except Exception:
            return FetchResult(ok=False, text=None)


def build_http() -> HttpClient:
    return UrllibHttpClient()
