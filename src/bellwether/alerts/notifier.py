import json
import urllib.request
from bellwether.ssl_ctx import SSL_CONTEXT
from bellwether.alerts.contracts import NotifyOutcome, Notifier


class WebhookNotifier:
    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout

    def notify(self, webhook_url: str, payload: dict) -> NotifyOutcome:
        text = payload.get("text") or json.dumps(payload)
        # Slack wants {"text": ...}, Discord wants {"content": ...} — send both; each ignores the other.
        body = json.dumps({"text": text, "content": text}).encode()
        req = urllib.request.Request(webhook_url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=SSL_CONTEXT) as resp:
                return NotifyOutcome(ok=200 <= resp.status < 300)
        except Exception:
            return NotifyOutcome(ok=False)


def build_notifier() -> Notifier:
    from bellwether.config import get_settings
    return WebhookNotifier(get_settings().alert_webhook_timeout_seconds)
