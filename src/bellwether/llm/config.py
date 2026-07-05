import dspy


def make_lm(model: str) -> dspy.LM:
    """Build a LiteLLM-backed DSPy LM for `model` (e.g. 'anthropic/claude-haiku-4-5').

    Provider-agnostic: LiteLLM routes by the model prefix and reads the provider's own
    credential from the environment. No provider key is passed here.
    """
    return dspy.LM(model)
