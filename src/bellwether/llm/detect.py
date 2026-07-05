import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import DetectionResult, Detector


class DetectSig(dspy.Signature):
    """Decide whether a public statement is market-relevant (could move asset prices)."""
    statement_text: str = dspy.InputField()
    is_relevant: bool = dspy.OutputField(desc="true if it could plausibly move markets")
    score: float = dspy.OutputField(desc="confidence 0.0-1.0 that it is market-relevant")


class Detect(dspy.Module):
    """Single-call relevance classifier (the Plan-5 compile target)."""

    def __init__(self):
        super().__init__()
        self.predict = dspy.Predict(DetectSig)

    def forward(self, statement_text: str) -> dspy.Prediction:
        return self.predict(statement_text=statement_text)


class _DetectorAdapter:
    """Maps the DSPy Prediction onto the frozen Detector contract."""

    def __init__(self, module: Detect, model: str, version: str):
        self._module = module
        self.model = model
        self.version = version

    def detect(self, statement_text: str) -> DetectionResult:
        pred = self._module(statement_text=statement_text)
        return DetectionResult(is_relevant=bool(pred.is_relevant), score=float(pred.score))


def build_detector(lm: dspy.LM | None = None, program_state: dict | None = None,
                   version: str = "baseline") -> Detector:
    settings = get_settings()
    module = Detect()
    module.set_lm(lm or make_lm(settings.detect_model))
    if program_state is not None:
        module.load_state(program_state)
    return _DetectorAdapter(module, settings.detect_model, version)
