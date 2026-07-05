from datetime import datetime
import pandas as pd
from bellwether.market.yfinance_adapter import _series_from_history
from bellwether.market.base import PriceSeries


def test_series_from_history_parses_and_utc():
    idx = pd.to_datetime([
        datetime(2026, 7, 1, 12, 0), datetime(2026, 7, 1, 12, 5),
    ])
    df = pd.DataFrame({"Close": [100.0, 110.0], "Volume": [10, 50]}, index=idx)
    series = _series_from_history(df)
    assert isinstance(series, PriceSeries)
    assert len(series.bars) == 2
    assert series.bars[0].price == 100.0 and series.bars[1].price == 110.0
    assert series.bars[0].volume == 10.0
    assert series.bars[0].ts.tzinfo is not None  # coerced to UTC
    assert series.bars[0].ts <= series.bars[1].ts  # ascending
