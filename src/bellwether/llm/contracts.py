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
