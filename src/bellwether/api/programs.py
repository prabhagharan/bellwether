from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.models.eval_run import EvalRun
from bellwether.programs import list_programs, set_champion
from bellwether.api.schemas import ProgramRead, EvalRunRead

router = APIRouter()


@router.get("/programs", response_model=list[ProgramRead])
def get_programs(module: str | None = None, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    return list_programs(session, module)


@router.post("/programs/{program_id}/promote", response_model=ProgramRead)
def promote_program(program_id: int, session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    program = set_champion(session, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


@router.get("/eval_runs", response_model=list[EvalRunRead])
def get_eval_runs(module: str | None = None, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    q = select(EvalRun)
    if module is not None:
        q = q.where(EvalRun.module == module)
    return list(session.execute(q.order_by(EvalRun.created_at.desc(), EvalRun.id.desc())).scalars())
