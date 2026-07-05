import dspy
from bellwether.eval.metrics import score_detection, score_extraction, GoldExtraction


def detect_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    score, feedback = score_detection(pred.is_relevant, gold.is_relevant)
    return dspy.Prediction(score=score, feedback=feedback)


def extract_metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    g = GoldExtraction(entities=list(gold.entities), direction=gold.direction,
                       magnitude=gold.magnitude, evidence_quote=gold.evidence_quote)
    score, feedback = score_extraction(pred, g, gold.statement_text)
    return dspy.Prediction(score=score, feedback=feedback)
