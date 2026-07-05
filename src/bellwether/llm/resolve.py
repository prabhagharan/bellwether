from typing import Literal
import dspy
from dspy.utils.exceptions import AdapterParseError
from bellwether.config import get_settings
from bellwether.llm.config import make_lm
from bellwether.llm.contracts import ResolveContext, ResolutionOutcome, Resolver


def normalize_entity(entity: str) -> str:
    return entity.strip().lower()


def _names_match(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


class ResolveSig(dspy.Signature):
    """Map a named entity to a tradable market symbol, using the figure and statement
    snippet as disambiguation context. Set is_tradable=false when there is no honest
    tradable instrument for the entity (e.g. an abstract concept)."""
    entity: str = dspy.InputField()
    figure_name: str = dspy.InputField()
    snippet: str = dspy.InputField()
    feedback: str = dspy.InputField(desc="why the previous attempt failed to verify; empty on the first try")
    is_tradable: bool = dspy.OutputField()
    symbol: str = dspy.OutputField(desc="ticker/pair, e.g. TSLA, GLD, BTC-USD; empty if not tradable")
    asset_class: Literal["equity", "etf", "index", "fx", "crypto"] = dspy.OutputField()
    instrument_name: str = dspy.OutputField(desc="the instrument's real name, e.g. Tesla Inc.")
    confidence: float = dspy.OutputField(desc="0.0-1.0")


class Resolve(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predict = dspy.ChainOfThought(ResolveSig)

    def forward(self, entity, figure_name, snippet, feedback) -> dspy.Prediction:
        return self.predict(entity=entity, figure_name=figure_name, snippet=snippet, feedback=feedback)


class _ResolverAdapter:
    def __init__(self, module: Resolve, model: str, verifier, max_attempts: int, threshold: float):
        self._module = module
        self.model = model
        self._verifier = verifier
        self._max_attempts = max_attempts
        self._threshold = threshold

    def resolve(self, entity: str, context: ResolveContext) -> ResolutionOutcome:
        feedback = ""
        for _ in range(self._max_attempts):
            try:
                pred = self._module(entity=entity, figure_name=context.figure_name,
                                    snippet=context.snippet, feedback=feedback)
            except AdapterParseError:
                feedback = "Your previous response could not be parsed. Return the fields exactly."
                continue
            if not bool(pred.is_tradable) or not str(pred.symbol).strip():
                return ResolutionOutcome(None, None, False, None, None)
            symbol = str(pred.symbol).strip()
            asset_class = str(pred.asset_class)
            confidence = float(pred.confidence)
            # Deterministic gate: yfinance verifies existence + name; MarketDataError propagates.
            info = self._verifier.lookup(symbol, asset_class)
            if info is not None and confidence >= self._threshold \
                    and _names_match(str(pred.instrument_name), info.name):
                return ResolutionOutcome(info.symbol, info.asset_class, True, info.name, confidence)
            candidates = self._verifier.search(str(pred.instrument_name) or entity)
            cand = ", ".join(f"{c.symbol} ({c.name})" for c in candidates[:5]) or "none"
            feedback = (f"Symbol {symbol!r} did not verify. Candidates: {cand}. "
                        f"Pick the correct symbol or set is_tradable=false.")
        return ResolutionOutcome(None, None, False, None, None)


def build_resolver(lm: dspy.LM | None = None, verifier=None,
                   max_attempts: int | None = None, threshold: float | None = None) -> Resolver:
    settings = get_settings()
    if verifier is None:
        from bellwether.market.registry import build_market_data
        verifier = build_market_data()
    module = Resolve()
    module.set_lm(lm or make_lm(settings.resolve_model))
    return _ResolverAdapter(
        module, settings.resolve_model, verifier,
        max_attempts if max_attempts is not None else settings.resolve_max_attempts,
        threshold if threshold is not None else settings.resolve_confidence_threshold,
    )
