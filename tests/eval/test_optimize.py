from bellwether.eval.optimize import promote_if_better, optimize, OptimizeResult
from bellwether.programs import save_program, set_champion, load_champion
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun
from sqlalchemy import select


def test_promote_if_better_strict():
    assert promote_if_better(0.8, 0.7) is True
    assert promote_if_better(0.7, 0.7) is False
    assert promote_if_better(0.6, 0.7) is False


def test_optimize_promotes_a_better_challenger(db_session):
    # no champion yet; fake compile returns a canned state; fake holdout: baseline 0.5, challenger 0.9
    def fake_compile(module, current_state, trainset): return {"trained": True}
    def fake_holdout(session, module, program_state, program_id):
        return 0.9 if program_state == {"trained": True} else 0.5  # challenger vs baseline

    res = optimize(db_session, "detect", compile_fn=fake_compile, evaluate_holdout_fn=fake_holdout)
    assert isinstance(res, OptimizeResult)
    assert res.version == 1 and abs(res.challenger_holdout - 0.9) < 1e-9 and res.promoted is True
    assert load_champion(db_session, "detect") == ({"trained": True}, 1)
    prog = db_session.execute(select(DspyProgram)).scalar_one()
    assert prog.holdout_score == 0.9 and prog.is_champion is True


def test_optimize_keeps_champion_when_challenger_not_better(db_session):
    champ = save_program(db_session, "detect", 1, {"old": True}, holdout_score=0.8, is_champion=True)
    db_session.flush()
    def fake_compile(module, current_state, trainset): return {"new": True}
    def fake_holdout(session, module, program_state, program_id): return 0.6  # worse than 0.8
    res = optimize(db_session, "detect", compile_fn=fake_compile, evaluate_holdout_fn=fake_holdout)
    assert res.promoted is False
    assert load_champion(db_session, "detect") == ({"old": True}, 1)  # unchanged
