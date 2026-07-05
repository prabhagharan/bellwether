import dspy
from sqlalchemy import select
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.relevance_label import RelevanceLabel
from bellwether.models.extraction_label import ExtractionLabel


def build_trainset(session: Session, module: str, split: str = "train") -> list[dspy.Example]:
    if module == "detect":
        rows = session.execute(
            select(Statement, RelevanceLabel)
            .join(RelevanceLabel, RelevanceLabel.statement_id == Statement.id)
            .where(RelevanceLabel.split == split)
        ).all()
        return [dspy.Example(statement_text=st.text, is_relevant=lab.is_relevant)
                .with_inputs("statement_text") for st, lab in rows]
    rows = session.execute(
        select(Statement, ExtractionLabel)
        .join(ExtractionLabel, ExtractionLabel.statement_id == Statement.id)
        .where(ExtractionLabel.split == split)
    ).all()
    return [dspy.Example(statement_text=st.text, entities=lab.entities, direction=lab.direction,
                         magnitude=lab.magnitude, evidence_quote=lab.evidence_quote)
            .with_inputs("statement_text") for st, lab in rows]


from dataclasses import dataclass
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.detect import Detect, build_detector
from bellwether.llm.extract import Extract, build_extractor
from bellwether.models.dspy_program import DspyProgram
from bellwether.programs import load_champion, save_program, next_version, set_champion
from bellwether.eval.evaluate import evaluate_detect, evaluate_extract
from bellwether.eval.gepa_metric import detect_metric, extract_metric


@dataclass(frozen=True)
class OptimizeResult:
    module: str
    version: int
    challenger_holdout: float
    champion_holdout: float
    promoted: bool


def promote_if_better(challenger_holdout: float, champion_holdout: float) -> bool:
    return challenger_holdout > champion_holdout


def optimize(session, module, *, compile_fn, evaluate_holdout_fn) -> OptimizeResult:
    trainset = build_trainset(session, module, "train")
    champ = load_champion(session, module)
    current_state = champ[0] if champ else None

    compiled_state = compile_fn(module, current_state, trainset)
    version = next_version(session, module)
    program = save_program(session, module, version, compiled_state,
                           holdout_score=None, is_champion=False)

    challenger_holdout = evaluate_holdout_fn(session, module, compiled_state, program.id)
    program.holdout_score = challenger_holdout

    if champ is not None:
        champ_prog = session.execute(
            select(DspyProgram).where(DspyProgram.module == module, DspyProgram.is_champion.is_(True))
        ).scalar_one()
        champion_holdout = (champ_prog.holdout_score if champ_prog.holdout_score is not None
                            else evaluate_holdout_fn(session, module, champ_prog.artifact, champ_prog.id))
    else:
        champion_holdout = evaluate_holdout_fn(session, module, None, None)  # baseline

    promoted = promote_if_better(challenger_holdout, champion_holdout)
    if promoted:
        set_champion(session, program.id)
    session.commit()
    return OptimizeResult(module, version, challenger_holdout, champion_holdout, promoted)


def gepa_compile(module: str, current_state: dict | None, trainset) -> dict:
    settings = get_settings()
    mod = Detect() if module == "detect" else Extract()
    mod.set_lm(make_lm(settings.detect_model if module == "detect" else settings.extract_model))
    if current_state is not None:
        mod.load_state(current_state)
    metric = detect_metric if module == "detect" else extract_metric
    gepa = dspy.GEPA(metric=metric, auto=settings.gepa_auto,
                     reflection_lm=make_lm(settings.reflection_model))
    compiled = gepa.compile(mod, trainset=trainset, valset=trainset)
    return compiled.dump_state()


def evaluate_holdout(session, module: str, program_state: dict | None, program_id: int | None) -> float:
    if module == "detect":
        det = build_detector(program_state=program_state, version="challenger")
        return evaluate_detect(session, det, "holdout", dspy_program_id=program_id).score
    ext = build_extractor(program_state=program_state, version="challenger")
    return evaluate_extract(session, ext, "holdout", dspy_program_id=program_id).score


def run_optimize(session, module: str) -> OptimizeResult:
    return optimize(session, module, compile_fn=gepa_compile, evaluate_holdout_fn=evaluate_holdout)
