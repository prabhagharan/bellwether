import json
import dspy
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.discovery.contracts import (
    WikidataEntity, SearchResult, Disambiguation, SourceCandidate, Discoverer,
)


class DisambiguateSig(dspy.Signature):
    """Pick which candidate entity the typed name refers to (or none)."""
    name: str = dspy.InputField()
    candidates: str = dspy.InputField(desc="JSON list of {qid,label,description}")
    qid: str = dspy.OutputField(desc="the chosen QID, or empty if none fit")
    confidence: float = dspy.OutputField(desc="0.0-1.0 confidence in the choice")


class GapfillSig(dspy.Signature):
    """Propose additional public sources for a figure that the known set lacks."""
    figure_name: str = dspy.InputField()
    known_sources: str = dspy.InputField(desc="JSON list of known source URLs")
    search_results: str = dspy.InputField(desc="JSON list of {title,url,snippet}")
    candidates: str = dspy.OutputField(desc='JSON list of {connector_type, config, rationale}')


class Discovery(dspy.Module):
    def __init__(self):
        super().__init__()
        self.disamb = dspy.Predict(DisambiguateSig)
        self.gap = dspy.Predict(GapfillSig)

    def forward_disambiguate(self, name, candidates):
        return self.disamb(name=name, candidates=candidates)

    def forward_gapfill(self, figure_name, known_sources, search_results):
        return self.gap(figure_name=figure_name, known_sources=known_sources, search_results=search_results)


class _DiscovererAdapter:
    def __init__(self, module: Discovery, model: str, version: str):
        self._m = module
        self.model = model
        self.version = version

    def disambiguate(self, name: str, candidates: list[WikidataEntity]) -> Disambiguation:
        payload = json.dumps([{"qid": c.qid, "label": c.label, "description": c.description} for c in candidates])
        pred = self._m.forward_disambiguate(name=name, candidates=payload)
        qid = (pred.qid or "").strip() or None
        return Disambiguation(qid=qid, confidence=float(pred.confidence))

    def gapfill(self, figure_name: str, known: list[str], results: list[SearchResult]) -> list[SourceCandidate]:
        pred = self._m.forward_gapfill(
            figure_name=figure_name,
            known_sources=json.dumps(known),
            search_results=json.dumps([{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results]),
        )
        try:
            raw = json.loads(pred.candidates)
        except (json.JSONDecodeError, TypeError):
            return []
        out = []
        for c in raw if isinstance(raw, list) else []:
            if isinstance(c, dict) and "connector_type" in c and "config" in c and isinstance(c["config"], dict):
                out.append(SourceCandidate(connector_type=str(c["connector_type"]),
                                           config=dict(c["config"]), rationale=str(c.get("rationale", ""))))
        return out


def build_discoverer(lm=None, program_state: dict | None = None, version: str = "baseline") -> Discoverer:
    settings = get_settings()
    module = Discovery()
    module.set_lm(lm or make_lm(settings.discovery_model))
    if program_state is not None:
        module.load_state(program_state)
    return _DiscovererAdapter(module, settings.discovery_model, version)
