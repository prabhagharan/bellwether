from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from bellwether.db import get_session
from bellwether.security.deps import get_current_user
from bellwether.models.user import User
from bellwether.eval.optimize import run_optimize
from bellwether.api.schemas import OptimizeRead

router = APIRouter()


@router.post("/optimize/{module}", response_model=OptimizeRead)
def optimize_module(module: str = Path(pattern="^(detect|extract)$"),
                    session: Session = Depends(get_session),
                    user: User = Depends(get_current_user)):
    result = run_optimize(session, module)
    return OptimizeRead(module=result.module, version=result.version,
                        challenger_holdout=result.challenger_holdout,
                        champion_holdout=result.champion_holdout, promoted=result.promoted)
