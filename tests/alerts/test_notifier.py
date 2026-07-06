from bellwether.alerts.contracts import NotifyOutcome, Notifier
from bellwether.alerts.notifier import WebhookNotifier, build_notifier


def test_contract_and_build():
    n = build_notifier()
    assert isinstance(n, Notifier)


def test_stub_notifier_satisfies_protocol():
    class Stub:
        def notify(self, webhook_url, payload): return NotifyOutcome(ok=True)
    assert isinstance(Stub(), Notifier)


def test_webhook_notifier_bad_url_returns_not_ok():
    # unreachable/invalid URL -> caught -> ok=False (never raises)
    out = WebhookNotifier(timeout=1.0).notify("http://127.0.0.1:0/nope", {"text": "hi"})
    assert isinstance(out, NotifyOutcome) and out.ok is False
