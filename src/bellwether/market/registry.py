from bellwether.market.base import MarketData
from bellwether.market.yfinance_adapter import YFinanceAdapter


def build_market_data() -> MarketData:
    """v1: the yfinance adapter serves all asset classes (crypto via X-USD pairs).
    A CoinGecko adapter for asset_class=crypto can slot in behind this factory later."""
    return YFinanceAdapter()
