from datetime import timedelta

_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_window(window: str) -> timedelta:
    """Parse a window like '5m' / '1h' / '1d' into a timedelta."""
    w = window.strip()
    if len(w) < 2 or not w[:-1].isdigit() or w[-1] not in _UNITS:
        raise ValueError(f"invalid window: {window!r}")
    return timedelta(**{_UNITS[w[-1]]: int(w[:-1])})


def parse_windows(spec: str) -> list[tuple[str, timedelta]]:
    """Parse a comma spec like '5m,1h,1d' into [(name, timedelta), ...]."""
    out: list[tuple[str, timedelta]] = []
    for part in spec.split(","):
        name = part.strip()
        if not name:
            continue
        out.append((name, parse_window(name)))
    return out
