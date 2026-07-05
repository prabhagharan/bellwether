from typing import Literal
import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import ExtractionResult, Extractor


class ExtractSig(dspy.Signature):
    """Extract a structured market signal from a public statement."""
    statement_text: str = dspy.InputField()
    entities: list[str] = dspy.OutputField(desc="tickers/companies/sectors named")
    direction: Literal["up", "down", "neutral"] = dspy.OutputField()
    magnitude: Literal["none", "small", "moderate", "large"] = dspy.OutputField()
    confidence: float = dspy.OutputField(desc="0.0-1.0")
    evidence_quote: str = dspy.OutputField(desc="a verbatim substring of statement_text")


class Extract(dspy.Module):
    """Single-call structured extractor (ChainOfThought adds a reasoning field only)."""

    def __init__(self):
        super().__init__()
        self.predict = dspy.ChainOfThought(ExtractSig)

    def forward(self, statement_text: str) -> dspy.Prediction:
        return self.predict(statement_text=statement_text)


class _ExtractorAdapter:
    """Maps the DSPy Prediction onto the frozen Extractor contract."""

    def __init__(self, module: Extract, model: str):
        self._module = module
        self.model = model

    def extract(self, statement_text: str) -> ExtractionResult:
        pred = self._module(statement_text=statement_text)
        return ExtractionResult(
            entities=list(pred.entities),
            direction=str(pred.direction),
            magnitude=str(pred.magnitude),
            confidence=float(pred.confidence),
            evidence_quote=str(pred.evidence_quote),
        )


def build_extractor(lm: dspy.LM | None = None) -> Extractor:
    settings = get_settings()
    module = Extract()
    module.set_lm(lm or make_lm(settings.extract_model))
    return _ExtractorAdapter(module, settings.extract_model)
