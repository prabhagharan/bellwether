from dspy.utils import DummyLM
from bellwether.llm.detect import build_detector
from bellwether.llm.contracts import DetectionResult


def test_detect_maps_prediction_to_result():
    # DummyLM returns canned output-field values; no network, no provider key.
    lm = DummyLM([{"is_relevant": "True", "score": "0.87"}])
    detector = build_detector(lm=lm)
    result = detector.detect("The central bank will raise rates.")
    assert isinstance(result, DetectionResult)
    assert result.is_relevant is True
    assert result.score == 0.87
    assert isinstance(detector.model, str) and detector.model
