from datetime import datetime, timezone
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.discovery.pipeline import run_discovery
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, XStatus, Disambiguation, SourceCandidate, FetchResult,
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
    db_session.add(f); db_session.flush()
    return f


def test_run_discovery_creates_verified_and_pending(db_session):
    f = _figure(db_session)
    run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                  x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
    db_session.flush()
    assert f.wikidata_id == "Q1" and "Jay" in f.aliases
    srcs = {s.connector_type: s for s in db_session.execute(
        select(Source).where(Source.figure_id == f.id)).scalars()}
    # website feed: wikidata(0.6)+domain_match(0.3, feed on official domain)+reachable(0.2) -> active
    assert srcs["rss"].status == "active" and srcs["rss"].verified is True and srcs["rss"].enabled is True
    # X handle: wikidata only (0.6), no key -> pending_review
    assert srcs["x"].status == "pending_review" and srcs["x"].enabled is False
    assert srcs["x"].discovery_meta["wikidata"] is True


def test_run_discovery_is_idempotent(db_session):
    f = _figure(db_session)
    for _ in range(2):
        run_discovery(db_session, f, wikidata=StubWikidata(), web_search=StubWeb(),
                      x_verifier=StubX(), discoverer=StubDiscoverer(), http=StubHttp())
        db_session.flush()
    n = len(db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().all())
    assert n == 2   # one rss + one x, no duplicates on re-run
