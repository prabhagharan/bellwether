from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DetectionResult:
    is_relevant: bool
    score: float


@dataclass(frozen=True)
class ExtractionResult:
    entities: list[str]
    direction: str
    magnitude: str
    confidence: float
    evidence_quote: str


@runtime_checkable
class Detector(Protocol):
    model: str
    def detect(self, statement_text: str) -> DetectionResult: ...


@runtime_checkable
class Extractor(Protocol):
    model: str
    def extract(self, statement_text: str) -> ExtractionResult: ...


class ExtractionParseError(Exception):
    """Raised by an Extractor when the model output cannot be parsed/validated into
    an ExtractionResult. A terminal condition (no retry); paradigm-agnostic so the
    stage layer never depends on a specific LLM library's exception types."""


@dataclass(frozen=True)
class ResolveContext:
    figure_name: str
    snippet: str


@dataclass(frozen=True)
class ResolutionOutcome:
    symbol: str | None
    asset_class: str | None
    measurable: bool
    instrument_name: str | None
    confidence: float | None


@runtime_checkable
class Resolver(Protocol):
    model: str
    def resolve(self, entity: str, context: ResolveContext) -> ResolutionOutcome: ...
