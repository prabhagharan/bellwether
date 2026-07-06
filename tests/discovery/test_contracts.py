# tests/discovery/test_contracts.py
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, SearchResult, XStatus, Disambiguation,
    SourceCandidate, FetchResult, SourceBinding, DiscoveryError,
    WikidataClient, WebSearch, XVerifier, Discoverer, HttpClient,
)


def test_dtos_hold_fields():
    e = WikidataEntity(qid="Q1", label="X", description="d")
    assert e.qid == "Q1"
    b = SourceBinding(connector_type="rss", config={"feed_url": "u"}, origin="discovered",
                      status="active", verified=True, discovery_confidence=0.9,
                      discovery_meta={"wikidata": True}, enabled=True)
    assert b.status == "active" and b.enabled is True
    assert issubclass(DiscoveryError, Exception)


def test_stub_satisfies_protocols():
    class W:
        def search(self, name): return []
        def claims(self, qid): return WikidataClaims(None, None, None, [])
    class H:
        def get(self, url): return FetchResult(ok=True, text="<html></html>")
    assert isinstance(W(), WikidataClient)
    assert isinstance(H(), HttpClient)
