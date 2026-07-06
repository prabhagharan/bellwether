# tests/test_worker_discovery.py
from sqlalchemy import select
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.worker import make_discovery_stage
from bellwether.discovery.contracts import WikidataEntity, WikidataClaims, Disambiguation, FetchResult, DiscoveryError


class W:
    def search(self, name): return [WikidataEntity("Q1", name, "d")]
    def claims(self, qid): return WikidataClaims("https://fed.gov", "fed", None, [])
class Web:
    def search(self, q): return []
class X:
    def verify(self, h): return None
class D:
    def disambiguate(self, n, c): return Disambiguation("Q1", 0.9)
    def gapfill(self, *a): return []
class Http:
    def get(self, url):
        return FetchResult(True, '<link rel="alternate" type="application/rss+xml" href="https://fed.gov/feed">' if url == "https://fed.gov" else "<rss></rss>")


def test_discovery_stage_processes_figure(db_session):
    f = Figure(name="Jerome Powell", type="individual", aliases=[], owner_id=None, discovery_status="pending")
    db_session.add(f); db_session.flush()
    stage = make_discovery_stage(wikidata=W(), web_search=Web(), x_verifier=X(), discoverer=D(), http=Http())
    claimed = stage.claim_next(db_session)
    stage.process(db_session, claimed)
    db_session.refresh(f)
    assert f.discovery_status == "done"
    assert db_session.execute(select(Source).where(Source.figure_id == f.id)).scalars().first() is not None


def test_discovery_stage_marks_failed_on_error(db_session):
    class BadW:
        def search(self, name): raise DiscoveryError("wikidata down")
        def claims(self, qid): raise DiscoveryError("x")
    f = Figure(name="X", type="individual", aliases=[], owner_id=None, discovery_status="pending")
    db_session.add(f); db_session.flush()
    stage = make_discovery_stage(wikidata=BadW(), web_search=Web(), x_verifier=X(), discoverer=D(), http=Http())
    stage.process(db_session, stage.claim_next(db_session))
    db_session.refresh(f)
    assert f.discovery_status == "failed" and "wikidata down" in (f.discovery_error or "")
