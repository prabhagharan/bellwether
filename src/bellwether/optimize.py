"""Manual optimization + program-versioning CLI (operator tool, not an API).

  python -m bellwether.optimize run <detect|extract>   # GEPA compile + champion/challenger; saves a new version, promotes iff better
  python -m bellwether.optimize programs [--module M]   # list versioned programs, newest first (* = champion)
  python -m bellwether.optimize promote <program_id>    # set a stored program as champion (promote / rollback)
  python -m bellwether.optimize evals [--module M]       # eval_runs scoreboard, newest first
"""
import argparse
from sqlalchemy import select
from bellwether.db import SessionLocal
from bellwether.eval.optimize import run_optimize
from bellwether.programs import list_programs, set_champion
from bellwether.models.eval_run import EvalRun


def _cmd_run(args) -> None:
    with SessionLocal() as session:
        r = run_optimize(session, args.module)  # commits internally
        print(f"module={r.module} version={r.version} "
              f"challenger_holdout={r.challenger_holdout:.4f} "
              f"champion_holdout={r.champion_holdout:.4f} promoted={r.promoted}")


def _cmd_programs(args) -> None:
    with SessionLocal() as session:
        for p in list_programs(session, args.module):
            star = "*" if p.is_champion else " "
            hs = f"{p.holdout_score:.4f}" if p.holdout_score is not None else "  -   "
            print(f"{star} {p.module:8} v{p.version:<4} holdout={hs} id={p.id}")


def _cmd_promote(args) -> None:
    with SessionLocal() as session:
        p = set_champion(session, args.program_id)
        if p is None:
            raise SystemExit(f"program {args.program_id} not found")
        session.commit()
        print(f"promoted: {p.module} v{p.version} (id={p.id}) is now champion")


def _cmd_evals(args) -> None:
    with SessionLocal() as session:
        q = select(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        if args.module:
            q = q.where(EvalRun.module == args.module)
        for r in session.execute(q).scalars():
            print(f"{r.module:8} {r.split:8} {r.metric:12} score={r.score:.4f} "
                  f"n={r.n} program_id={r.dspy_program_id}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="bellwether.optimize",
                                     description="Manual optimization + program versioning")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run GEPA optimize + champion/challenger for a module")
    p_run.add_argument("module", choices=["detect", "extract"])
    p_run.set_defaults(func=_cmd_run)

    p_prog = sub.add_parser("programs", help="list versioned programs (newest first; * = champion)")
    p_prog.add_argument("--module", choices=["detect", "extract"], default=None)
    p_prog.set_defaults(func=_cmd_programs)

    p_prom = sub.add_parser("promote", help="set a program as champion (promote / rollback)")
    p_prom.add_argument("program_id", type=int)
    p_prom.set_defaults(func=_cmd_promote)

    p_eval = sub.add_parser("evals", help="list eval_runs (scoreboard)")
    p_eval.add_argument("--module", choices=["detect", "extract"], default=None)
    p_eval.set_defaults(func=_cmd_evals)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
