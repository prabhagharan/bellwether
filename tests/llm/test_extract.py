import pytest
from dspy.utils import DummyLM
from dspy.utils.exceptions import AdapterParseError
from bellwether.llm.extract import build_extractor
from bellwether.llm.contracts import ExtractionResult, ExtractionParseError


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


class _FakeSignature:
    output_fields = {}


class _RaisingModule:
    """Stub standing in for the DSPy module — its __call__ raises the DSPy parse
    error, so the test only needs to assert the adapter's translation, not any
    real LM behavior."""

    def __call__(self, *args, **kwargs):
        raise AdapterParseError(
            adapter_name="ChatAdapter", signature=_FakeSignature(), lm_response="garbled")


def test_extract_translates_dspy_parse_error_to_contract_error():
    lm = DummyLM([{
        "reasoning": "irrelevant — module is stubbed out below",
        "entities": '["TSLA"]',
        "direction": "up",
        "magnitude": "moderate",
        "confidence": "0.8",
        "evidence_quote": "Tesla will grow",
    }])
    extractor = build_extractor(lm=lm)
    extractor._module = _RaisingModule()

    with pytest.raises(ExtractionParseError):
        extractor.extract("Tesla will grow next quarter.")
