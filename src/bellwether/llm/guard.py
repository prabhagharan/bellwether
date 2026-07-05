def is_verbatim(quote: str, source_text: str) -> bool:
    """True iff `quote` is a non-empty literal substring of `source_text`.

    The structural anti-fabrication guarantee: an extracted evidence_quote that is
    not a verbatim substring of the original statement is rejected in code.
    """
    if quote is None or not quote.strip():
        return False
    return quote in source_text
