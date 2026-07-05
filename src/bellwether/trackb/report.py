from sqlalchemy import select, func
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact


def avg_pct_move_by_figure(session: Session) -> list[tuple[int, float]]:
    """Minimal Track-B aggregation: mean measured pct_move per figure. (Leaderboard is Plan 7.)"""
    rows = session.execute(
        select(Statement.figure_id, func.avg(Impact.pct_move))
        .join(Extraction, Extraction.statement_id == Statement.id)
        .join(Resolution, Resolution.extraction_id == Extraction.id)
        .join(Impact, Impact.resolution_id == Resolution.id)
        .where(Impact.status == "measured", Impact.pct_move.isnot(None))
        .group_by(Statement.figure_id)
    ).all()
    return [(fid, float(avg)) for fid, avg in rows]
