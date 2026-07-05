from datetime import timedelta
import pytest
from bellwether.windows import parse_window, parse_windows


def test_parse_window_units():
    assert parse_window("5m") == timedelta(minutes=5)
    assert parse_window("1h") == timedelta(hours=1)
    assert parse_window("1d") == timedelta(days=1)


def test_parse_window_rejects_bad():
    with pytest.raises(ValueError):
        parse_window("5x")
    with pytest.raises(ValueError):
        parse_window("h")


def test_parse_windows_list():
    assert parse_windows("5m,1h,1d") == [
        ("5m", timedelta(minutes=5)), ("1h", timedelta(hours=1)), ("1d", timedelta(days=1))
    ]
    assert parse_windows("5m, ,1d") == [("5m", timedelta(minutes=5)), ("1d", timedelta(days=1))]
