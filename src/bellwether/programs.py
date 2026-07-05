from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from bellwether.models.dspy_program import DspyProgram


def next_version(session: Session, module: str) -> int:
    mx = session.execute(
        select(func.max(DspyProgram.version)).where(DspyProgram.module == module)
    ).scalar()
    return (mx or 0) + 1


def save_program(session: Session, module: str, version: int, artifact: dict,
                 holdout_score: float | None = None, is_champion: bool = False) -> DspyProgram:
    program = DspyProgram(module=module, version=version, artifact=artifact,
                          holdout_score=holdout_score, is_champion=is_champion)
    session.add(program)
    session.flush()
    return program


def load_champion(session: Session, module: str) -> tuple[dict, int] | None:
    program = session.execute(
        select(DspyProgram).where(DspyProgram.module == module, DspyProgram.is_champion.is_(True))
    ).scalar_one_or_none()
    return (program.artifact, program.version) if program is not None else None


def set_champion(session: Session, program_id: int) -> DspyProgram | None:
    program = session.get(DspyProgram, program_id)
    if program is None:
        return None
    session.execute(
        update(DspyProgram).where(DspyProgram.module == program.module).values(is_champion=False)
    )
    program.is_champion = True
    session.flush()
    return program


def list_programs(session: Session, module: str | None = None) -> list[DspyProgram]:
    query = select(DspyProgram)
    if module is not None:
        query = query.where(DspyProgram.module == module)
    return list(session.execute(query.order_by(DspyProgram.module, DspyProgram.version.desc())).scalars())
