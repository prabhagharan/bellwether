# tests/test_worker_resolve_measure.py
from datetime import datetime, timezone
from sqlalchemy import select, func
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.entity_symbol import EntitySymbol
from bellwether.models.impact import Impact
from bellwether.llm.contracts import ResolutionOutcome
from bellwether.windows import parse_windows
from bellwether.worker import make_resolve_stage

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
