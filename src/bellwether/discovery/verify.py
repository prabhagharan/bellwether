SIGNAL_WEIGHTS = {"wikidata": 0.6, "domain_match": 0.3, "x_verified": 0.2, "reachable": 0.2}


def score_binding(signals: dict, threshold: float) -> tuple[float, bool]:
    confidence = min(1.0, sum(w for k, w in SIGNAL_WEIGHTS.items() if signals.get(k)))
    return confidence, confidence >= threshold
