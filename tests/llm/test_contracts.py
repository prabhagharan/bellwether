from bellwether.llm.contracts import DetectionResult, ExtractionResult, Detector, Extractor


def test_detection_result_holds_fields():
    r = DetectionResult(is_relevant=True, score=0.9)
    assert r.is_relevant is True and r.score == 0.9


def test_extraction_result_holds_fields():
    r = ExtractionResult(entities=["TSLA"], direction="up", magnitude="moderate",
                         confidence=0.8, evidence_quote="q")
    assert r.entities == ["TSLA"] and r.direction == "up" and r.evidence_quote == "q"


def test_stub_satisfies_protocols():
    class StubDetector:
        model = "stub/detect"
        def detect(self, statement_text): return DetectionResult(True, 1.0)

    class StubExtractor:
        model = "stub/extract"
        def extract(self, statement_text):
            return ExtractionResult([], "neutral", "none", 0.0, "")

    assert isinstance(StubDetector(), Detector)
    assert isinstance(StubExtractor(), Extractor)
