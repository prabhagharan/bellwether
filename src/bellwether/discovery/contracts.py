# src/bellwether/discovery/contracts.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class WikidataEntity:
    qid: str
    label: str
    description: str


@dataclass(frozen=True)
class WikidataClaims:
    website: str | None
    x_username: str | None
    youtube_channel: str | None
    aliases: list[str]


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class XStatus:
    exists: bool
    verified: bool


@dataclass(frozen=True)
class Disambiguation:
    qid: str | None
    confidence: float


@dataclass(frozen=True)
class SourceCandidate:
    connector_type: str
    config: dict
    rationale: str


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    text: str | None


@dataclass
class SourceBinding:
    connector_type: str
    config: dict
    origin: str
    status: str
    verified: bool
    discovery_confidence: float
    discovery_meta: dict
    enabled: bool


class DiscoveryError(Exception):
    """Transient external failure — the discovery run is retryable."""


@runtime_checkable
class WikidataClient(Protocol):
    def search(self, name: str) -> list[WikidataEntity]: ...
    def claims(self, qid: str) -> WikidataClaims: ...


@runtime_checkable
class WebSearch(Protocol):
    def search(self, query: str) -> list[SearchResult]: ...


@runtime_checkable
class XVerifier(Protocol):
    def verify(self, handle: str) -> XStatus | None: ...


@runtime_checkable
class Discoverer(Protocol):
    def disambiguate(self, name: str, candidates: list[WikidataEntity]) -> Disambiguation: ...
    def gapfill(self, figure_name: str, known: list[str], results: list[SearchResult]) -> list[SourceCandidate]: ...


@runtime_checkable
class HttpClient(Protocol):
    def get(self, url: str) -> FetchResult: ...
