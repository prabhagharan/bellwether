from datetime import datetime, timezone
import yfinance as yf
from bellwether.market.base import (
    InstrumentInfo, SymbolCandidate, PriceBar, PriceSeries, MarketDataError,
)

_INTERVAL = {"5m": "5m", "1h": "60m", "1d": "1d"}


def _series_from_history(df) -> PriceSeries:
    bars: list[PriceBar] = []
    for ts, row in df.iterrows():
        py_ts = ts.to_pydatetime()
        if py_ts.tzinfo is None:
            py_ts = py_ts.replace(tzinfo=timezone.utc)
        vol = row.get("Volume", 0)
        bars.append(PriceBar(ts=py_ts, price=float(row["Close"]),
                             volume=float(vol if vol == vol and vol is not None else 0.0)))
    bars.sort(key=lambda b: b.ts)
    return PriceSeries(bars=bars)


class YFinanceAdapter:
    def lookup(self, symbol: str, asset_class: str) -> InstrumentInfo | None:
        try:
            info = yf.Ticker(symbol).info
        except Exception as exc:  # network/parse — transient
            raise MarketDataError(str(exc)) from exc
        name = info.get("longName") or info.get("shortName")
        if not name:
            return None
        return InstrumentInfo(symbol=symbol, name=name, asset_class=asset_class)

    def search(self, query: str) -> list[SymbolCandidate]:
        try:
            quotes = yf.Search(query).quotes
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc
        out: list[SymbolCandidate] = []
        for q in quotes:
            sym = q.get("symbol")
            if sym:
                out.append(SymbolCandidate(symbol=sym,
                                           name=q.get("longname") or q.get("shortname") or ""))
        return out

    def price_series(self, symbol, asset_class, start, end, window) -> PriceSeries:
        interval = _INTERVAL.get(window, "1d")
        try:
            df = yf.Ticker(symbol).history(start=start, end=end, interval=interval)
        except Exception as exc:
            raise MarketDataError(str(exc)) from exc
        return _series_from_history(df)
