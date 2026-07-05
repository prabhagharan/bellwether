# src/bellwether/worker.py
from dataclasses import dataclass
from typing import Callable
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.detection import Detection
from bellwether.models.extraction import Extraction
from bellwether.llm.contracts import Detector, Extractor
from bellwether.llm.guard import is_verbatim
import argparse
import logging
import signal
import threading
from bellwether.db import SessionLocal
from bellwether.config import get_settings
from bellwether.queue import claim_one, reclaim_stale
from bellwether.llm.detect import build_detector
from bellwether.llm.extract import build_extractor

logger = logging.getLogger(__name__)


@dataclass
class Stage:
    name: str
    claim_from: str
    claim_to: str
    process: Callable[[Session, Statement], None]


def make_detect_stage(detector: Detector, threshold: float) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        result = detector.detect(statement.text)
        session.add(Detection(
            statement_id=statement.id,
            is_relevant=result.is_relevant,
            score=result.score,
            model=detector.model,
            version="baseline",
        ))
        statement.status = "detected" if (result.is_relevant and result.score >= threshold) else "irrelevant"
        statement.claimed_at = None
        session.commit()

    return Stage(name="detect", claim_from="new", claim_to="detecting", process=process)


def make_extract_stage(extractor: Extractor) -> Stage:
    def process(session: Session, statement: Statement) -> None:
        try:
            result = extractor.extract(statement.text)
        except Exception:
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        # Verbatim-substring guard at the stage boundary — outside the module, so no
        # extractor implementation can ever land a fabricated quote.
        if not is_verbatim(result.evidence_quote, statement.text):
            statement.status = "extract_failed"
            statement.claimed_at = None
            session.commit()
            return
        session.add(Extraction(
            statement_id=statement.id,
            entities=result.entities,
            direction=result.direction,
            magnitude=result.magnitude,
            confidence=result.confidence,
            evidence_quote=result.evidence_quote,
            model=extractor.model,
            version="baseline",
        ))
        statement.status = "extracted"
        statement.claimed_at = None
        session.commit()

    return Stage(name="extract", claim_from="detected", claim_to="extracting", process=process)


def run_worker(stage: Stage, *, session_factory=SessionLocal, poll_interval=None,
               once: bool = False, stop_event: "threading.Event | None" = None) -> int:
    settings = get_settings()
    if poll_interval is None:
        poll_interval = settings.worker_poll_interval_seconds

    # Startup crash-recovery: return rows stuck in this stage's in-flight marker.
    with session_factory() as session:
        reclaim_stale(session, stage.claim_to, stage.claim_from,
                      settings.worker_stale_reclaim_seconds)

    processed = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        with session_factory() as session:
            statement = claim_one(session, stage.claim_from, stage.claim_to)
            if statement is not None:
                try:
                    stage.process(session, statement)
                    processed += 1
                except Exception:
                    session.rollback()  # claim already committed; row reclaimed later
                    logger.exception("stage %s failed for statement id=%s",
                                     stage.name, statement.id)
        if statement is None:
            if once:
                break
            if stop_event is not None:
                if stop_event.wait(poll_interval):
                    break
            else:
                import time
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
