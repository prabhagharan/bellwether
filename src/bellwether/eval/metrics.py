from dataclasses import dataclass
from bellwether.llm.guard import is_verbatim


@dataclass(frozen=True)
class GoldExtraction:
    entities: list[str]
    direction: str
    magnitude: str
    evidence_quote: str


def score_detection(pred_is_relevant: bool, gold_is_relevant: bool) -> tuple[float, str]:
    if bool(pred_is_relevant) == bool(gold_is_relevant):
        return 1.0, "ok"
    return 0.0, f"relevance wrong: pred={bool(pred_is_relevant)} gold={bool(gold_is_relevant)}"


def _entity_f1(pred: list[str], gold: list[str]) -> float:
    p = {e.strip().lower() for e in pred if e and e.strip()}
    g = {e.strip().lower() for e in gold if e and e.strip()}
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    inter = len(p & g)
    if inter == 0:
        return 0.0
    precision = inter / len(p)
    recall = inter / len(g)
    return 2 * precision * recall / (precision + recall)


def score_extraction(pred, gold: GoldExtraction, statement_text: str) -> tuple[float, str]:
    d = 1.0 if str(pred.direction) == str(gold.direction) else 0.0
    m = 1.0 if str(pred.magnitude) == str(gold.magnitude) else 0.0
    e = 1.0 if is_verbatim(pred.evidence_quote, statement_text) else 0.0
    ef = _entity_f1(list(pred.entities), list(gold.entities))
    score = (d + m + e + ef) / 4.0
    parts = []
    if d == 0.0:
        parts.append(f"direction wrong (pred {pred.direction}, gold {gold.direction})")
    if m == 0.0:
        parts.append(f"magnitude wrong (pred {pred.magnitude}, gold {gold.magnitude})")
    if e == 0.0:
        parts.append("evidence_quote not a verbatim substring")
    if ef < 1.0:
        parts.append(f"entities off (F1={ef:.2f})")
    return score, ("ok" if not parts else "; ".join(parts))
