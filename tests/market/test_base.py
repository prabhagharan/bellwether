from datetime import datetime, timezone
from bellwether.market.base import (
    InstrumentInfo, SymbolCandidate, PriceBar, PriceSeries,
    MarketDataError, SymbolVerifier, MarketData,
)


def test_dataclasses_hold_fields():
    info = InstrumentInfo(symbol="TSLA", name="Tesla Inc", asset_class="equity")
    assert info.symbol == "TSLA" and info.name == "Tesla Inc"
    bar = PriceBar(ts=datetime(2026, 7, 1, tzinfo=timezone.utc), price=1.0, volume=2.0)
    assert PriceSeries(bars=[bar]).bars[0].price == 1.0
    assert SymbolCandidate(symbol="TSLA", name="Tesla").symbol == "TSLA"


def test_market_data_error_is_exception():
    assert issubclass(MarketDataError, Exception)


def test_stub_satisfies_protocols():
    class Stub:
        def lookup(self, symbol, asset_class): return None
        def search(self, query): return []
        def price_series(self, symbol, asset_class, start, end, window): return PriceSeries(bars=[])
    assert isinstance(Stub(), SymbolVerifier)
    assert isinstance(Stub(), MarketData)
