# src/bellwether/worker.py
from dataclasses import dataclass
from typing import Callable
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.llm.contracts import Detector, Extractor, ExtractionParseError
from bellwether.llm.guard import is_verbatim
import argparse
import logging
import signal
import threading
import time
from bellwether.db import SessionLocal
from bellwether.config import get_settings
from bellwether.queue import claim_one, reclaim_stale
from bellwether.llm.detect import build_detector
from bellwether.llm.extract import build_extractor

logger = logging.getLogger(__name__)


@dataclass
class Stage:
    name: str
    claim_next: Callable[[Session], object | None]
    reclaim: Callable[[Session, float], int]
    process: Callable[[Session, object], None]


def make_detect_stage(detector: Detector, threshold: float) -> Stage:
    def process(session: Session, statement) -> None:
        result = detector.detect(statement.text)
        session.add(Detection(
            statement_id=statement.id, is_relevant=result.is_relevant, score=result.score,
            model=detector.model, version="baseline",
        ))
        statement.status = "detected" if (result.is_relevant and result.score >= threshold) else "irrelevant"
        statement.claimed_at = None
        session.commit()

    return Stage(
        name="detect",
        claim_next=lambda s: claim_one(s, "new", "detecting"),
        reclaim=lambda s, secs: reclaim_stale(s, "detecting", "new", secs),
        process=process,
    )


def make_extract_stage(extractor: Extractor) -> Stage:
    def process(session: Session, statement) -> None:
        try:
            result = extractor.extract(statement.text)
        except ExtractionParseError:
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        if not is_verbatim(result.evidence_quote, statement.text):
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        session.add(Extraction(
            statement_id=statement.id, entities=result.entities, direction=result.direction,
            magnitude=result.magnitude, confidence=result.confidence,
            evidence_quote=result.evidence_quote, model=extractor.model, version="baseline",
        ))
        statement.status = "extracted"
        statement.claimed_at = None
        session.commit()

    return Stage(
        name="extract",
        claim_next=lambda s: claim_one(s, "detected", "extracting"),
        reclaim=lambda s, secs: reclaim_stale(s, "extracting", "detected", secs),
        process=process,
    )


def run_worker(stage: Stage, *, session_factory=SessionLocal, poll_interval=None,
               reclaim_interval_seconds: float | None = None,
               once: bool = False, stop_event: "threading.Event | None" = None) -> int:
    settings = get_settings()
    if poll_interval is None:
        poll_interval = settings.worker_poll_interval_seconds
    if reclaim_interval_seconds is None:
        reclaim_interval_seconds = settings.worker_stale_reclaim_seconds

    with session_factory() as session:
        stage.reclaim(session, settings.worker_stale_reclaim_seconds)
    last_reclaim = time.monotonic()

    processed = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        if time.monotonic() - last_reclaim >= reclaim_interval_seconds:
            with session_factory() as session:
                stage.reclaim(session, settings.worker_stale_reclaim_seconds)
            last_reclaim = time.monotonic()
        with session_factory() as session:
            row = stage.claim_next(session)
            if row is not None:
                try:
                    stage.process(session, row)
                    processed += 1
                except Exception:
                    session.rollback()
                    logger.exception("stage %s failed for row id=%s",
                                     stage.name, getattr(row, "id", "?"))
        if row is None:
            if once:
                break
            if stop_event is not None:
                if stop_event.wait(poll_interval):
                    break
            else:
                time.sleep(poll_interval)
    return processed


def _build_stage(name: str) -> Stage:
    settings = get_settings()
    if name == "detect":
        return make_detect_stage(build_detector(), settings.relevance_threshold)
    return make_extract_stage(build_extractor())


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="bellwether.worker")
    parser.add_argument("stage", choices=["detect", "extract"])
    parser.add_argument("--once", action="store_true",
                        help="drain the queue once and exit (default: run as a daemon)")
    args = parser.parse_args(argv)

    stop_event = threading.Event()

    def _handle(signum, frame):
        logger.info("received signal %s — finishing in-flight item then exiting", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    stage = _build_stage(args.stage)
    count = run_worker(stage, once=args.once, stop_event=stop_event)
    logger.info("worker %s processed %d statement(s)", args.stage, count)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
