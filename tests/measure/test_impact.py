from datetime import datetime, timedelta, timezone
from bellwether.market.base import PriceSeries, PriceBar
from bellwether.measure.impact import compute_impact, ImpactPoint

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _series(points):
    return PriceSeries(bars=[PriceBar(ts=t, price=p, volume=v) for t, p, v in points])


def test_rise_and_volume_spike():
    s = _series([
        (T0 - timedelta(minutes=2), 100.0, 10.0),
        (T0 - timedelta(minutes=1), 100.0, 10.0),
        (T0,                        100.0, 10.0),
        (T0 + timedelta(minutes=5), 110.0, 50.0),
    ])
    r = compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3)
    assert isinstance(r, ImpactPoint)
    assert r.price_t0 == 100.0 and r.price_after == 110.0
    assert abs(r.pct_move - 0.10) < 1e-9
    assert abs(r.volume_spike - 5.0) < 1e-9  # 50 / avg(10,10,10)


def test_insufficient_after_bar_returns_none():
    s = _series([(T0 - timedelta(minutes=1), 100.0, 10.0), (T0, 100.0, 10.0)])
    assert compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3) is None


def test_no_prior_bar_returns_none():
    s = _series([(T0 + timedelta(minutes=5), 110.0, 50.0)])
    assert compute_impact(s, T0, timedelta(minutes=5), baseline_bars=3) is None


def test_fall_move():
    s = _series([(T0, 100.0, 10.0), (T0 + timedelta(hours=1), 90.0, 10.0)])
    r = compute_impact(s, T0, timedelta(hours=1), baseline_bars=1)
    assert abs(r.pct_move + 0.10) < 1e-9  # -10%
