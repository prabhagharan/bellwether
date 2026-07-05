from dataclasses import dataclass
from datetime import datetime, timedelta
from bellwether.market.base import PriceSeries


@dataclass(frozen=True)
class ImpactPoint:
    price_t0: float
    price_after: float
    pct_move: float
    volume_spike: float


def compute_impact(series: PriceSeries, event_at: datetime, window: timedelta,
                   baseline_bars: int) -> ImpactPoint | None:
    """Realized move over `window` after `event_at`. None if the series can't bracket it."""
    bars = sorted(series.bars, key=lambda b: b.ts)
    if not bars:
        return None
    after_target = event_at + window
    prior = [b for b in bars if b.ts <= event_at]
    after = [b for b in bars if b.ts >= after_target]
    if not prior or not after:
        return None
    b0 = prior[-1]
    b1 = after[0]
    if b0.price == 0:
        return None
    pct_move = (b1.price - b0.price) / b0.price
    window_bars = [b for b in bars if event_at < b.ts <= after_target]
    window_volume = sum(b.volume for b in window_bars) if window_bars else b1.volume
    baseline = prior[-baseline_bars:]
    baseline_avg = sum(b.volume for b in baseline) / len(baseline) if baseline else 0.0
    volume_spike = window_volume / baseline_avg if baseline_avg > 0 else 0.0
    return ImpactPoint(price_t0=b0.price, price_after=b1.price,
                       pct_move=pct_move, volume_spike=volume_spike)
