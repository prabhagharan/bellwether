from dataclasses import dataclass
from bellwether.eval.metrics import GoldExtraction, score_detection, score_extraction

SRC = "Tesla will grow production and deliveries this quarter."


@dataclass
class Pred:
    entities: list
    direction: str
    magnitude: str
    evidence_quote: str


def test_score_detection():
    assert score_detection(True, True)[0] == 1.0
    s, fb = score_detection(True, False)
    assert s == 0.0 and "relevance" in fb


def test_extraction_perfect():
    gold = GoldExtraction(["TSLA"], "up", "moderate", "Tesla will grow")
    pred = Pred(["tsla"], "up", "moderate", "Tesla will grow")
    s, fb = score_extraction(pred, gold, SRC)
    assert s == 1.0 and fb == "ok"


def test_extraction_partial_and_feedback():
    gold = GoldExtraction(["TSLA"], "up", "moderate", "Tesla will grow")
    # wrong direction, non-verbatim quote, entities miss -> only magnitude(1) + entityF1(0) ... compute:
    pred = Pred(["FORD"], "down", "moderate", "Tesla will SHRINK")
    s, fb = score_extraction(pred, gold, SRC)
    # direction 0, magnitude 1, evidence 0 (not a substring), entityF1 0 -> mean = 0.25
    assert abs(s - 0.25) < 1e-9
    assert "direction" in fb and "evidence" in fb and "entities" in fb


def test_entity_f1_partial():
    gold = GoldExtraction(["TSLA", "GM"], "up", "small", "Tesla will grow")
    pred = Pred(["TSLA"], "up", "small", "Tesla will grow")
    s, fb = score_extraction(pred, gold, SRC)
    # d1 m1 e1 ; entityF1 = 2*(1/1)*(1/2)/(1/1+1/2)=0.6667 -> mean=(1+1+1+0.6667)/4=0.9167
    assert abs(s - 0.91666666) < 1e-6
