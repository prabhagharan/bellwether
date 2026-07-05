from bellwether.llm.guard import is_verbatim

SOURCE = "The central bank will raise rates. Inflation remains elevated."


def test_exact_substring_passes():
    assert is_verbatim("will raise rates", SOURCE) is True


def test_non_substring_fails():
    assert is_verbatim("will cut rates", SOURCE) is False


def test_empty_or_whitespace_fails():
    assert is_verbatim("", SOURCE) is False
    assert is_verbatim("   ", SOURCE) is False


def test_full_text_passes():
    assert is_verbatim(SOURCE, SOURCE) is True
