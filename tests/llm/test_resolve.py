from dspy.utils import DummyLM
from bellwether.llm.resolve import build_resolver, normalize_entity
from bellwether.llm.contracts import ResolveContext, ResolutionOutcome
from bellwether.market.base import InstrumentInfo, SymbolCandidate

CTX = ResolveContext(figure_name="Elon Musk", snippet="Tesla will grow next quarter.")


class StubVerifier:
    def __init__(self, infos): self._infos = infos          # {symbol: InstrumentInfo|None}
    def lookup(self, symbol, asset_class): return self._infos.get(symbol)
    def search(self, query): return [SymbolCandidate("TSLA", "Tesla Inc")]


def _answer(**kw):
    base = {"reasoning": "…", "is_tradable": "True", "symbol": "TSLA",
            "asset_class": "equity", "instrument_name": "Tesla", "confidence": "0.9"}
    base.update(kw)
    return base


def test_normalize_entity():
    assert normalize_entity("  Tesla ") == "tesla"


def test_accepts_verified_symbol():
    lm = DummyLM([_answer()])
    verifier = StubVerifier({"TSLA": InstrumentInfo("TSLA", "Tesla Inc", "equity")})
    out = build_resolver(lm=lm, verifier=verifier).resolve("Tesla", CTX)
    assert isinstance(out, ResolutionOutcome)
    assert out.measurable is True and out.symbol == "TSLA" and out.asset_class == "equity"


def test_not_tradable_is_non_measurable():
    lm = DummyLM([_answer(is_tradable="False", symbol="")])
    out = build_resolver(lm=lm, verifier=StubVerifier({})).resolve("monetary policy", CTX)
    assert out.measurable is False and out.symbol is None


def test_gives_up_after_max_attempts_when_unverified():
    # verifier always returns None -> every attempt fails -> non-measurable
    lm = DummyLM([_answer(), _answer()])
    out = build_resolver(lm=lm, verifier=StubVerifier({}), max_attempts=2).resolve("Tesla", CTX)
    assert out.measurable is False
