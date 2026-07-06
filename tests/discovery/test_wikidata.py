import pytest
from bellwether.discovery.wikidata import _parse_search, _parse_claims, WikidataAdapter
from bellwether.discovery.contracts import FetchResult, DiscoveryError


def test_parse_search():
    payload = {"search": [
        {"id": "Q13129", "label": "Jerome Powell", "description": "American attorney"},
        {"id": "Q42", "label": "Other", "description": ""},
    ]}
    ents = _parse_search(payload)
    assert [e.qid for e in ents] == ["Q13129", "Q42"]
    assert ents[0].label == "Jerome Powell"


def test_parse_claims():
    payload = {"entities": {"Q1": {
        "claims": {
            "P856": [{"mainsnak": {"datavalue": {"value": "https://federalreserve.gov"}}}],
            "P2002": [{"mainsnak": {"datavalue": {"value": "federalreserve"}}}],
            "P2397": [{"mainsnak": {"datavalue": {"value": "UCabc"}}}],
        },
        "aliases": {"en": [{"value": "Jay Powell"}]},
    }}}
    claims = _parse_claims(payload)
    assert claims.website == "https://federalreserve.gov"
    assert claims.x_username == "federalreserve"
    assert claims.youtube_channel == "UCabc"
    assert claims.aliases == ["Jay Powell"]


def test_adapter_raises_discovery_error_on_fetch_failure():
    class BadHttp:
        def get(self, url): return FetchResult(ok=False, text=None)
    with pytest.raises(DiscoveryError):
        WikidataAdapter(BadHttp()).search("anything")
