from bellwether.discovery.xverify import _parse_x, build_x_verifier
from bellwether.discovery.contracts import XStatus


def test_parse_x():
    assert _parse_x({"data": {"verified": True}}) == XStatus(exists=True, verified=True)
    assert _parse_x({}) == XStatus(exists=False, verified=False)


def test_build_x_verifier_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("X_API_KEY", raising=False)
    xv = build_x_verifier()
    assert xv.verify("jack") is None   # unavailable without a key
