import pytest
import bellwether.optimize as cli
from bellwether.eval.optimize import OptimizeResult
from bellwether.programs import save_program
from bellwether.models.dspy_program import DspyProgram
from bellwether.models.eval_run import EvalRun


class _FakeSessionCM:
    def __init__(self, sess): self._sess = sess
    def __enter__(self): return self._sess
    def __exit__(self, *a): return False


@pytest.fixture
def use_db_session(monkeypatch, db_session):
    monkeypatch.setattr(cli, "SessionLocal", lambda: _FakeSessionCM(db_session))
    return db_session


def test_run_prints_result_no_gepa(use_db_session, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_optimize",
                        lambda session, module: OptimizeResult(module, 3, 0.95, 0.80, True))
    cli.main(["run", "extract"])
    out = capsys.readouterr().out
    assert "version=3" in out and "promoted=True" in out and "0.9500" in out


def test_run_rejects_unknown_module(use_db_session):
    with pytest.raises(SystemExit):
        cli.main(["run", "bogus"])


def test_programs_lists_newest_first_with_champion(use_db_session, capsys):
    db = use_db_session
    save_program(db, "detect", 1, {"a": 1}, holdout_score=0.7, is_champion=False)
    save_program(db, "detect", 2, {"a": 2}, holdout_score=0.9, is_champion=True)
    db.flush()
    cli.main(["programs", "--module", "detect"])
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out
    champion_line = next(ln for ln in out.splitlines() if "v2" in ln)
    assert champion_line.lstrip().startswith("*")  # champion marked


def test_promote_sets_champion(use_db_session, capsys):
    db = use_db_session
    p = save_program(db, "detect", 1, {"a": 1}, holdout_score=0.7, is_champion=False)
    db.flush()
    cli.main(["promote", str(p.id)])
    out = capsys.readouterr().out
    assert "is now champion" in out
    db.refresh(p)
    assert p.is_champion is True


def test_promote_missing_program(use_db_session):
    with pytest.raises(SystemExit):
        cli.main(["promote", "999999"])


def test_evals_lists(use_db_session, capsys):
    db = use_db_session
    db.add(EvalRun(module="extract", dspy_program_id=None, split="holdout",
                   metric="extract_avg", score=0.8, n=5))
    db.flush()
    cli.main(["evals", "--module", "extract"])
    out = capsys.readouterr().out
    assert "extract" in out and "0.8000" in out
