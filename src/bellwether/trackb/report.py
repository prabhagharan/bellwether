from sqlalchemy import select, func
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.extraction import Extraction
from bellwether.models.resolution import Resolution
from bellwether.models.impact import Impact
from bellwether.models.figure import Figure


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


def leaderboard_by_figure(session: Session, owner_id) -> list[dict]:
    rows = session.execute(
        select(Figure.id, Figure.name, Extraction.direction, Impact.pct_move)
        .join(Statement, Statement.figure_id == Figure.id)
        .join(Extraction, Extraction.statement_id == Statement.id)
        .join(Resolution, Resolution.extraction_id == Extraction.id)
        .join(Impact, Impact.resolution_id == Resolution.id)
        .where(Figure.owner_id == owner_id, Impact.status == "measured", Impact.pct_move.isnot(None))
    ).all()
    agg: dict[int, dict] = {}
    for fid, fname, direction, pct in rows:
        a = agg.setdefault(fid, {"figure_id": fid, "figure_name": fname, "moves": [], "hits": 0})
        a["moves"].append(pct)
        if (direction == "up" and pct > 0) or (direction == "down" and pct < 0):
            a["hits"] += 1
    out = []
    for a in agg.values():
        n = len(a["moves"])
        out.append({
            "figure_id": a["figure_id"], "figure_name": a["figure_name"], "n": n,
            "avg_pct_move": sum(a["moves"]) / n,
            "avg_abs_pct_move": sum(abs(m) for m in a["moves"]) / n,
            "directional_hit_rate": a["hits"] / n,
        })
    return sorted(out, key=lambda r: r["avg_abs_pct_move"], reverse=True)
