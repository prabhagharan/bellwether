from dspy.utils import DummyLM
from bellwether.llm.extract import build_extractor
from bellwether.llm.contracts import ExtractionResult


def test_extract_maps_prediction_to_result():
    lm = DummyLM([{
        "reasoning": "Tesla guidance is bullish.",
        "entities": '["TSLA"]',
        "direction": "up",
        "magnitude": "moderate",
        "confidence": "0.8",
        "evidence_quote": "Tesla will grow",
    }])
    extractor = build_extractor(lm=lm)
    result = extractor.extract("Tesla will grow next quarter.")
    assert isinstance(result, ExtractionResult)
    assert result.entities == ["TSLA"]
    assert result.direction == "up"
    assert result.magnitude == "moderate"
    assert result.confidence == 0.8
    assert result.evidence_quote == "Tesla will grow"
    assert isinstance(extractor.model, str) and extractor.model
