from urllib.parse import urljoin
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.config import get_settings
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.discovery.contracts import SourceBinding
from bellwether.discovery.verify import score_binding
from bellwether.discovery.connectors import youtube_feed_url, x_binding, domain_of, discover_feed_links
from bellwether.connectors.registry import DISCOVERABLE_CONNECTOR_TYPES


def _identity(connector_type: str, config: dict) -> str:
    if connector_type == "rss":
        return config.get("feed_url", "")
    if connector_type == "x":
        return config.get("handle", "")
    return str(sorted(config.items()))


def _feed_for_website(website: str, http) -> str | None:
    res = http.get(website)
    if res.ok and res.text:
        links = discover_feed_links(res.text)
        if links:
            return urljoin(website, links[0])
    for path in ("/feed", "/rss", "/feed.xml", "/rss.xml"):
        candidate = urljoin(website, path)
        probe = http.get(candidate)
        if probe.ok and probe.text and ("<rss" in probe.text or "<feed" in probe.text):
            return candidate
    return None


def _reachable(url: str, http) -> bool:
    res = http.get(url)
    return bool(res.ok and res.text and ("<rss" in res.text or "<feed" in res.text))


def run_discovery(session: Session, figure: Figure, *, wikidata, web_search, x_verifier, discoverer, http) -> None:
    settings = get_settings()
    threshold = settings.discovery_confidence_threshold

    candidates = wikidata.search(figure.name)
    official_domain = None
    ambiguous = False
    known: list[str] = []
    bindings: list[SourceBinding] = []

    if candidates:
        disamb = discoverer.disambiguate(figure.name, candidates)
        qid = disamb.qid or candidates[0].qid
        ambiguous = disamb.qid is None or disamb.confidence < 0.5
        figure.wikidata_id = qid
        claims = wikidata.claims(qid)
        if claims.aliases:
            figure.aliases = sorted(set(list(figure.aliases) + claims.aliases))
        if claims.website:
            official_domain = domain_of(claims.website)
            known = [claims.website]

        # website -> rss feed (feed auto-discovery)
        if claims.website:
            feed = _feed_for_website(claims.website, http)
            if feed:
                signals = {"wikidata": True, "domain_match": domain_of(feed) == official_domain,
                           "reachable": _reachable(feed, http)}
                bindings.append(_binding("rss", {"feed_url": feed}, signals, threshold, ambiguous))
        # youtube -> rss feed (independent, additive)
        if claims.youtube_channel:
            feed = youtube_feed_url(claims.youtube_channel)
            signals = {"wikidata": True, "reachable": _reachable(feed, http)}
            bindings.append(_binding("rss", {"feed_url": feed}, signals, threshold, ambiguous))
        # x handle
        if claims.x_username:
            ct, cfg = x_binding(claims.x_username)
            xs = x_verifier.verify(claims.x_username)
            signals = {"wikidata": True,
                       "domain_match": False,
                       "x_verified": bool(xs and xs.verified)}
            bindings.append(_binding(ct, cfg, signals, threshold, ambiguous))

    # gap-fill (LLM + web search) — proposals, must pass verification.
    # Runs even with NO Wikidata match (spec §8): such candidates can only ever be
    # pending_review (no wikidata signal), but the path must not be silently dead.
    results = web_search.search(f"{figure.name} official blog rss feed")
    for cand in discoverer.gapfill(figure.name, known, results):
        # Drop LLM proposals whose connector_type has no registered connector: they can never
        # ingest (build_connector would raise UnknownConnectorType), so persisting them would
        # create a source that looks active but silently fetches nothing.
        if cand.connector_type not in DISCOVERABLE_CONNECTOR_TYPES:
            continue
        # A candidate's address may live under feed_url (rss) or url (other types); read either.
        cand_url = cand.config.get("feed_url") or cand.config.get("url") or ""
        reachable = _reachable(cand_url, http) if cand.connector_type == "rss" else False
        signals = {"domain_match": official_domain is not None and domain_of(cand_url) == official_domain,
                   "reachable": reachable}
        bindings.append(_binding(cand.connector_type, cand.config, signals, threshold, ambiguous, source="tavily"))

    _upsert(session, figure, bindings)


def _binding(connector_type, config, signals, threshold, ambiguous, source="wikidata") -> SourceBinding:
    confidence, signal_verified = score_binding(signals, threshold)
    active = signal_verified and not ambiguous
    meta = {"source": source, **{k: signals.get(k) for k in ("wikidata", "domain_match", "x_verified", "reachable")}}
    return SourceBinding(
        connector_type=connector_type, config=config, origin="discovered",
        status="active" if active else "pending_review", verified=active,
        discovery_confidence=confidence, discovery_meta=meta, enabled=active,
    )


def _upsert(session: Session, figure: Figure, bindings: list[SourceBinding]) -> None:
    existing = {(_identity(s.connector_type, s.config)): s for s in session.execute(
        select(Source).where(Source.figure_id == figure.id, Source.origin == "discovered")).scalars()}
    for b in bindings:
        key = _identity(b.connector_type, b.config)
        row = existing.get(key)
        if row is None:
            session.add(Source(
                figure_id=figure.id, connector_type=b.connector_type, config=b.config,
                provenance="primary", origin="discovered", enabled=b.enabled,
                status=b.status, verified=b.verified,
                discovery_confidence=b.discovery_confidence, discovery_meta=b.discovery_meta,
                owner_id=figure.owner_id,
            ))
        else:  # re-run: only refresh rows still awaiting review. A human decision —
               # confirm (-> status="active") or reject (-> "rejected") — is never overwritten.
            if row.status == "pending_review":
                row.status, row.verified = b.status, b.verified
                row.discovery_confidence, row.discovery_meta = b.discovery_confidence, b.discovery_meta
                row.enabled = b.enabled
