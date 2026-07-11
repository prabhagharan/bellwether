from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.discovery.pipeline import run_discovery
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, Disambiguation, FetchResult, SearchResult, SourceCandidate,
)


class StubWikidata:
    def search(self, name): return [WikidataEntity("Q1", name, "desc")]
    def claims(self, qid):
        return WikidataClaims(website="https://fed.gov", x_username="fed",
                              youtube_channel="UCabc", aliases=["Jay"])


class StubWeb:
    def search(self, query): return []


class StubX:
    def verify(self, handle): return None   # no key


class StubDiscoverer:
    def disambiguate(self, name, candidates): return Disambiguation(qid="Q1", confidence=0.95)
    def gapfill(self, figure_name, known, results): return []


class StubHttp:
    def get(self, url):
        # website has an RSS feed link; feed reachable
        if url == "https://fed.gov":
            return FetchResult(ok=True, text='<link rel="alternate" type="application/rss+xml" href="https://fed.gov/feed">')
        return FetchResult(ok=True, text="<rss></rss>")


def _figure(db_session):
    f = Figure(name="Jerome Powell", type="individual", aliases=[], owner_id=None,
               discovery_status="running")
    db_session.add(f)
    db_session.flush()
    return f


def test_run_discovery_creates_verified_and_pending(db_session):
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                  x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
    db_session.flush()
    assert f.wikidata_id == "Q1" and "Jay" in f.aliases
    all_srcs = db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all()
    rss_srcs = [s for s in all_srcs if s.connector_type == "rss"]
    x_srcs = [s for s in all_srcs if s.connector_type == "x"]

    # website feed + youtube feed are both independent, additive rss sources
    assert len(rss_srcs) == 2
    for s in rss_srcs:
        assert s.status == "active" and s.verified is True and s.enabled is True
    feed_urls = {s.config["feed_url"] for s in rss_srcs}
    assert "https://fed.gov/feed" in feed_urls
    assert any("channel_id=UCabc" in url for url in feed_urls)

    # X handle: wikidata only (0.6), no key -> pending_review
    assert len(x_srcs) == 1
    assert x_srcs[0].status == "pending_review" and x_srcs[0].enabled is False
    assert x_srcs[0].discovery_meta["wikidata"] is True


def test_run_discovery_is_idempotent(db_session):
    f = _figure(db_session)
    for _ in range(2):
        run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                      x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
        db_session.flush()
    n = len(db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all())
    assert n == 3   # two rss (website + youtube) + one x, no duplicates on re-run


def test_rerun_preserves_confirmed_source(db_session):
    """A human 'confirm' (pending_review -> active) must survive re-discovery, not be downgraded."""
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                  x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
    db_session.flush()
    # simulate the review endpoint confirming the pending X source
    x = db_session.execute(
        select(Source).where(Source.figure_id == f.id, Source.connector_type == "x")).scalar_one()
    x.status, x.enabled, x.verified = "active", True, True
    db_session.flush()
    # re-run discovery — the confirmed source must NOT revert to pending_review/disabled
    run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                  x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
    db_session.flush()
    db_session.refresh(x)
    assert x.status == "active" and x.enabled is True and x.verified is True


class NoMatchWikidata:
    def search(self, name): return []
    def claims(self, qid): raise AssertionError("claims should not be called with no candidates")


class GapWeb:
    def search(self, query): return [SearchResult("Blog", "https://blog.example.com/feed", "posts")]


class GapDiscoverer:
    def disambiguate(self, name, candidates): raise AssertionError("disambiguate needs candidates")
    def gapfill(self, figure_name, known, results):
        return [SourceCandidate(connector_type="rss",
                                config={"feed_url": "https://blog.example.com/feed"},
                                rationale="found via search")]


def test_gapfill_runs_without_wikidata_match(db_session):
    """No Wikidata match -> gap-fill still runs (spec §8); its proposals are pending_review only."""
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=NoMatchWikidata(), web_search=GapWeb(),
                  x_verifier=StubX(), discoverer=GapDiscoverer(), http=StubHttp())
    db_session.flush()
    srcs = db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all()
    assert len(srcs) == 1
    assert srcs[0].connector_type == "rss"
    assert srcs[0].status == "pending_review" and srcs[0].enabled is False   # LLM proposal cannot auto-enable
    assert srcs[0].discovery_meta["source"] == "tavily"


class UnknownTypeDiscoverer:
    """gap-fill returns a valid rss plus two types no connector can ingest."""
    def disambiguate(self, name, candidates): return Disambiguation(qid="Q1", confidence=0.95)
    def gapfill(self, figure_name, known, results):
        return [
            SourceCandidate(connector_type="rss",
                            config={"feed_url": "https://blog.example.com/feed"}, rationale="valid"),
            SourceCandidate(connector_type="twitter",
                            config={"url": "https://x.com/foo", "handle": "foo"}, rationale="dupe of x"),
            SourceCandidate(connector_type="government_website",
                            config={"url": "https://gov.example/news"}, rationale="no connector exists"),
        ]


def test_gapfill_drops_unregistered_connector_types(db_session):
    """BUG #2: the LLM proposes connector types with no registered connector. They can never
    ingest (UnknownConnectorType) and must NOT be persisted as sources — only rss/x survive."""
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=NoMatchWikidata(), web_search=GapWeb(),
                  x_verifier=StubX(), discoverer=UnknownTypeDiscoverer(), http=StubHttp())
    db_session.flush()
    srcs = db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all()
    assert [s.connector_type for s in srcs] == ["rss"]          # twitter + government_website dropped
    assert srcs[0].config["feed_url"] == "https://blog.example.com/feed"


class SiteOnlyWikidata:
    """Establishes an official domain (website) but no x/youtube, so gap-fill is the only extra."""
    def search(self, name): return [WikidataEntity("Q1", name, "desc")]
    def claims(self, qid):
        return WikidataClaims(website="https://fed.gov", x_username=None, youtube_channel=None, aliases=[])


class UrlKeyedRssDiscoverer:
    def disambiguate(self, name, candidates): return Disambiguation(qid="Q1", confidence=0.95)
    def gapfill(self, figure_name, known, results):
        # an rss candidate whose address is under 'url' (not 'feed_url'), on the official domain
        return [SourceCandidate(connector_type="rss",
                                config={"url": "https://fed.gov/press-feed"}, rationale="on official domain")]


def test_gapfill_domain_match_reads_url_key(db_session):
    """BUG #1: domain_match must recognise a candidate's address whether it's under feed_url or
    url. A gap-fill source on the figure's official domain should earn the domain_match signal."""
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=SiteOnlyWikidata(), web_search=GapWeb(),
                  x_verifier=StubX(), discoverer=UrlKeyedRssDiscoverer(), http=StubHttp())
    db_session.flush()
    gap = [s for s in db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars()
           if s.discovery_meta.get("source") == "tavily"]
    assert len(gap) == 1
    assert gap[0].discovery_meta["domain_match"] is True       # fed.gov == official domain
    assert gap[0].discovery_confidence >= 0.3                  # at least the domain_match weight


class NewsProposingDiscoverer:
    def disambiguate(self, name, candidates): return Disambiguation(qid="Q1", confidence=0.95)
    def gapfill(self, figure_name, known, results):
        return [
            SourceCandidate(connector_type="rss",
                            config={"feed_url": "https://blog.example.com/feed"}, rationale="ok"),
            SourceCandidate(connector_type="news",
                            config={"query": "X"}, rationale="LLM tried to propose a news source"),
        ]


def test_gapfill_does_not_propose_news_source(db_session):
    """`news` is auto-created per figure, not discovered. Even though it's a registered,
    ingestable connector type, the discovery gap-fill must NOT create a news source
    (that would duplicate the auto-created one)."""
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=NoMatchWikidata(), web_search=GapWeb(),
                  x_verifier=StubX(), discoverer=NewsProposingDiscoverer(), http=StubHttp())
    db_session.flush()
    srcs = db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all()
    assert [s.connector_type for s in srcs] == ["rss"]        # rss kept, proposed news dropped
