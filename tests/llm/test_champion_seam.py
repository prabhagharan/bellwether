import dspy
from sqlalchemy import select
from datetime import datetime, timezone
from bellwether.llm.detect import Detect, build_detector
from bellwether.llm.extract import Extract, build_extractor
from bellwether.models.figure import Figure
from bellwether.models.source import Source
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.llm.contracts import DetectionResult
from bellwether.worker import make_detect_stage


def test_build_detector_default_version_baseline():
    assert build_detector().version == "baseline"


def test_build_detector_loads_program_and_stamps_version():
    state = Detect().dump_state()  # a valid state dict; no network
    d = build_detector(program_state=state, version="7")
    assert d.version == "7"


def test_build_extractor_default_version_baseline():
    assert build_extractor().version == "baseline"


class _StubDetector:
    model = "stub/detect"
    version = "9"
    def detect(self, text): return DetectionResult(True, 0.9)


def test_detect_stage_stamps_detector_version(db_session):
    f = Figure(name="F", type="individual", aliases=[], owner_id=None); db_session.add(f); db_session.flush()
    s = Source(figure_id=f.id, connector_type="rss", config={}, provenance="primary", origin="manual", owner_id=None)
    db_session.add(s); db_session.flush()
    st = Statement(figure_id=f.id, source_id=s.id, external_id="e", text="rates rise", url=None,
                   provenance="primary", published_at=datetime(2026, 7, 1, tzinfo=timezone.utc), status="detecting")
    db_session.add(st); db_session.flush()
    make_detect_stage(_StubDetector(), threshold=0.5).process(db_session, st)
    d = db_session.execute(select(Detection).where(Detection.statement_id == st.id)).scalar_one()
    assert d.version == "9"
