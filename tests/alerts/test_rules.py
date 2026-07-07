from dataclasses import dataclass
from bellwether.alerts.rules import matches


@dataclass
class Ex:
    confidence: float
    magnitude: str
    direction: str


def test_empty_condition_matches_all():
    assert matches({}, Ex(0.1, "none", "neutral"), 5) is True


def test_min_confidence():
    e = Ex(0.6, "large", "up")
    assert matches({"min_confidence": 0.7}, e, 1) is False
    assert matches({"min_confidence": 0.5}, e, 1) is True


def test_min_magnitude_ordinal():
    assert matches({"min_magnitude": "moderate"}, Ex(0.9, "small", "up"), 1) is False
    assert matches({"min_magnitude": "moderate"}, Ex(0.9, "large", "up"), 1) is True


def test_directions_and_figures():
    e = Ex(0.9, "large", "up")
    assert matches({"directions": ["down"]}, e, 1) is False
    assert matches({"directions": ["up", "down"]}, e, 1) is True
    assert matches({"figure_ids": [2, 3]}, e, 1) is False
    assert matches({"figure_ids": [1, 2]}, e, 1) is True


def test_all_anded():
    e = Ex(0.9, "large", "up")
    assert matches({"min_confidence": 0.7, "min_magnitude": "moderate",
                    "directions": ["up"], "figure_ids": [1]}, e, 1) is True
    assert matches({"min_confidence": 0.7, "directions": ["down"]}, e, 1) is False
