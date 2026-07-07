_MAGNITUDE_RANK = {"none": 0, "small": 1, "moderate": 2, "large": 3}


def matches(condition: dict, extraction, figure_id: int) -> bool:
    min_conf = condition.get("min_confidence")
    if min_conf is not None and extraction.confidence < min_conf:
        return False
    min_mag = condition.get("min_magnitude")
    if min_mag is not None and _MAGNITUDE_RANK.get(extraction.magnitude, -1) < _MAGNITUDE_RANK.get(min_mag, 0):
        return False
    directions = condition.get("directions")
    if directions and extraction.direction not in directions:
        return False
    figure_ids = condition.get("figure_ids")
    if figure_ids and figure_id not in figure_ids:
        return False
    return True
