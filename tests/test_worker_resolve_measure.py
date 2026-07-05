# tests/test_worker_resolve_measure.py
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, delete
from bellwether.db import SessionLocal
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact
from bellwether.models.user import User
from bellwether.models.detection import Detection
from bellwether.llm.contracts import ResolutionOutcome
from bellwether.market.base import PriceSeries, PriceBar, MarketDataError
from bellwether.windows import parse_windows
from bellwether.worker import make_measure_stage, make_resolve_stage, run_worker

WINDOWS = parse_windows("5m,1h,1d")


class StubResolver:
    model = "stub/resolve"
    def __init__(self, outcomes, record=None):
        self._outcomes = outcomes           # {entity: ResolutionOutcome}
        self.calls = record if record is not None else []
    def resolve(self, entity, context):
        self.calls.append(entity)
        return self._outcomes[entity]


def _extracted(db_session, entities):
    f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=None)
    db_session.add(f); db_session.flush()
    src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                 origin="manual", owner_id=None); db_session.add(src); db_session.flush()
    st = Statement(figure_id=f.id, source_id=src.id, external_id="e", text="Tesla will grow.",
                   url=None, provenance="primary",
                   published_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc), status="resolving")
    db_session.add(st); db_session.flush()
    ex = Extraction(statement_id=st.id, entities=entities, direction="up", magnitude="small",
                    confidence=0.5, evidence_quote="Tesla", model="m", version="baseline")
    db_session.add(ex); db_session.flush()
    return st, ex


def test_resolve_writes_rows_and_pending_impacts(db_session):
    st, ex = _extracted(db_session, ["Tesla", "monetary policy"])
    outcomes = {
        "Tesla": ResolutionOutcome("TSLA", "equity", True, "Tesla Inc", 0.9),
        "monetary policy": ResolutionOutcome(None, None, False, None, None),
    }
    stage = make_resolve_stage(StubResolver(outcomes), WINDOWS)
    stage.process(db_session, st)

    assert st.status == "resolved" and st.claimed_at is None
    res = db_session.execute(select(Resolution).where(Resolution.extraction_id == ex.id)).scalars().all()
    assert {r.entity: r.measurable for r in res} == {"Tesla": True, "monetary policy": False}
    # 3 pending impacts for the measurable entity only, with correct due_at
    imps = db_session.execute(select(Impact)).scalars().all()
    assert len(imps) == 3
    assert {i.window for i in imps} == {"5m", "1h", "1d"}
    assert all(i.status == "pending" and i.symbol == "TSLA" for i in imps)
    assert all(i.event_at == st.published_at and i.due_at > i.event_at for i in imps)
    # entity_symbols cache populated for both (yes and no)
    assert db_session.execute(select(func.count()).select_from(EntitySymbol)).scalar_one() == 2


def test_resolve_uses_cache_on_second_entity(db_session):
    # pre-seed the cache; the resolver must NOT be called
    db_session.add(EntitySymbol(normalized_entity="tesla", symbol="TSLA", asset_class="equity",
                                measurable=True, instrument_name="Tesla Inc", confidence=0.9, source="llm"))
    db_session.flush()
    st, ex = _extracted(db_session, ["Tesla"])
    calls = []
    stage = make_resolve_stage(StubResolver({}, record=calls), WINDOWS)
    stage.process(db_session, st)
    assert calls == []  # cache hit — resolver never invoked
    assert db_session.execute(select(func.count()).select_from(Impact)).scalar_one() == 3


class StubMarket:
    def __init__(self, series=None, exc=None):
        self._series, self._exc = series, exc
        self.last_call = None
    def lookup(self, symbol, asset_class): return None
    def search(self, query): return []
    def price_series(self, symbol, asset_class, start, end, window):
        self.last_call = dict(symbol=symbol, asset_class=asset_class, start=start, end=end, window=window)
        if self._exc is not None:
            raise self._exc
        return self._series


def _pending_impact(db_session):
    st, ex = _extracted(db_session, ["Tesla"])
    st.status = "resolved"
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    t0 = st.published_at
    imp = Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="5m",
                 event_at=t0, due_at=t0 + timedelta(minutes=5), status="measuring")  # pre-claimed
    db_session.add(imp); db_session.flush()
    return imp, t0


def test_measure_fills_impact(db_session):
    imp, t0 = _pending_impact(db_session)
    series = PriceSeries(bars=[
        PriceBar(ts=t0, price=100.0, volume=10.0),
        PriceBar(ts=t0 + timedelta(minutes=5), price=110.0, volume=50.0),
    ])
    make_measure_stage(StubMarket(series=series), baseline_bars=20).process(db_session, imp)
    assert imp.status == "measured" and imp.claimed_at is None and imp.measured_at is not None
    assert imp.price_t0 == 100.0 and imp.price_after == 110.0
    assert abs(imp.pct_move - 0.10) < 1e-9


def test_measure_insufficient_data_is_terminal(db_session):
    imp, t0 = _pending_impact(db_session)
    make_measure_stage(StubMarket(series=PriceSeries(bars=[])), baseline_bars=20).process(db_session, imp)
    assert imp.status == "measure_failed"


def test_measure_transient_error_propagates(db_session):
    imp, t0 = _pending_impact(db_session)
    stage = make_measure_stage(StubMarket(exc=MarketDataError("timeout")), baseline_bars=20)
    with pytest.raises(MarketDataError):
        stage.process(db_session, imp)
    assert imp.status == "measuring"  # left for reclaim-retry, not burned


def test_measure_scales_lookback_to_window_and_baseline(db_session):
    st, ex = _extracted(db_session, ["Tesla"])
    st.status = "resolved"
    r = Resolution(extraction_id=ex.id, entity="Tesla", symbol="TSLA", asset_class="equity", measurable=True)
    db_session.add(r); db_session.flush()
    t0 = st.published_at
    imp = Impact(resolution_id=r.id, symbol="TSLA", asset_class="equity", window="1d",
                 event_at=t0, due_at=t0 + timedelta(days=1), status="measuring")  # pre-claimed
    db_session.add(imp); db_session.flush()
    stub = StubMarket(series=PriceSeries(bars=[]))
    make_measure_stage(stub, baseline_bars=20).process(db_session, imp)
    assert stub.last_call is not None
    assert stub.last_call["start"] <= imp.event_at - timedelta(days=20)
    # The fetch end must extend past due_at so the first bar at/after due_at is included
    # even when it lands on a coarse-bar boundary or across a weekend (regression: a
    # `due_at + 1s` end fetches no "after" bar for daily data, so the 1d window never measures).
    assert stub.last_call["end"] >= imp.due_at + timedelta(days=1)


def test_end_to_end_resolve_then_measure():
    # real Postgres + SessionLocal; past-dated statement so all windows are already due
    def _clear():
        with SessionLocal() as s:
            for m in (Impact, Resolution, EntitySymbol, Extraction, Detection, Statement, Source, Figure, User):
                s.execute(delete(m))
            s.commit()
    _clear()
    try:
        with SessionLocal() as s:
            f = Figure(name="Elon Musk", type="individual", aliases=[], owner_id=None); s.add(f); s.flush()
            src = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary",
                         origin="manual", owner_id=None); s.add(src); s.flush()
            t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # well in the past -> due
            st = Statement(figure_id=f.id, source_id=src.id, external_id="e2e", text="Tesla will grow.",
                           url=None, provenance="primary", published_at=t0, status="extracted"); s.add(st); s.flush()
            s.add(Extraction(statement_id=st.id, entities=["Tesla"], direction="up", magnitude="small",
                             confidence=0.5, evidence_quote="Tesla", model="m", version="baseline"))
            s.commit()
        resolver = StubResolver({"Tesla": ResolutionOutcome("TSLA", "equity", True, "Tesla Inc", 0.9)})
        assert run_worker(make_resolve_stage(resolver, WINDOWS), once=True) == 1
        series = PriceSeries(bars=[
            PriceBar(ts=t0, price=100.0, volume=10.0),
            PriceBar(ts=t0 + timedelta(days=1), price=90.0, volume=10.0),
        ])
        measured = run_worker(make_measure_stage(StubMarket(series=series), baseline_bars=20), once=True)
        assert measured == 3  # 5m/1h/1d all due
        with SessionLocal() as s:
            statuses = {i.window: i.status for i in s.execute(select(Impact)).scalars()}
            assert statuses["1d"] == "measured"
    finally:
        _clear()
