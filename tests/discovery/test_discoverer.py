import dspy
from bellwether.discovery.discoverer import build_discoverer
from bellwether.discovery.contracts import WikidataEntity, SearchResult, Disambiguation, SourceCandidate


def test_disambiguate_with_dummy_lm():
    lm = dspy.utils.DummyLM([{"qid": "Q13129", "confidence": "0.92"}])
    d = build_discoverer(lm=lm)
    out = d.disambiguate("Jerome Powell", [WikidataEntity("Q13129", "Jerome Powell", "Fed chair")])
    assert isinstance(out, Disambiguation) and out.qid == "Q13129" and abs(out.confidence - 0.92) < 1e-6


def test_gapfill_with_dummy_lm():
    lm = dspy.utils.DummyLM([{"candidates": '[{"connector_type": "rss", "config": {"feed_url": "https://x.com/feed"}, "rationale": "official blog"}]'}])
    d = build_discoverer(lm=lm)
    out = d.gapfill("Jerome Powell", ["https://federalreserve.gov"],
                    [SearchResult("Blog", "https://x.com/feed", "posts")])
    assert len(out) == 1 and isinstance(out[0], SourceCandidate)
    assert out[0].connector_type == "rss" and out[0].config == {"feed_url": "https://x.com/feed"}
