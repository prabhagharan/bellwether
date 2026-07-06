import json
import os
import urllib.request
from bellwether.discovery.contracts import XStatus, XVerifier, DiscoveryError


def _parse_x(payload: dict) -> XStatus:
    data = payload.get("data") or {}
    return XStatus(exists=bool(data), verified=bool(data.get("verified", False)))


class XVerifierAdapter:
    def __init__(self, api_key: str | None, timeout: float = 10.0):
        self._api_key = api_key
        self._timeout = timeout

    def verify(self, handle: str) -> XStatus | None:
        if not self._api_key:
            return None
        url = f"https://api.twitter.com/2/users/by/username/{handle.lstrip('@')}?user.fields=verified"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            raise DiscoveryError("x verify request failed") from exc
        return _parse_x(payload)


def build_x_verifier() -> XVerifier:
    return XVerifierAdapter(os.environ.get("X_API_KEY"))
