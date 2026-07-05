from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    asset_class: str


@dataclass(frozen=True)
class SymbolCandidate:
    symbol: str
    name: str


@dataclass(frozen=True)
class PriceBar:
    ts: datetime
    price: float
    volume: float


@dataclass(frozen=True)
class PriceSeries:
    bars: list[PriceBar]


class MarketDataError(Exception):
    """Transient market-data adapter failure (network/timeout/rate-limit). Paradigm-
    agnostic so the stage/worker layer never imports a provider's exception types."""


@runtime_checkable
class SymbolVerifier(Protocol):
    def lookup(self, symbol: str, asset_class: str) -> InstrumentInfo | None: ...
    def search(self, query: str) -> list[SymbolCandidate]: ...


@runtime_checkable
class MarketData(SymbolVerifier, Protocol):
    def price_series(self, symbol: str, asset_class: str,
                     start: datetime, end: datetime, window: str) -> PriceSeries: ...
