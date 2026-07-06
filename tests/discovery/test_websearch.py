from bellwether.discovery.websearch import _parse_tavily, build_web_search


def test_parse_tavily():
    payload = {"results": [
        {"title": "T1", "url": "https://a.com", "content": "snippet one"},
        {"title": "T2", "url": "https://b.com", "content": "snippet two"},
    ]}
    rs = _parse_tavily(payload)
    assert [r.url for r in rs] == ["https://a.com", "https://b.com"]
    assert rs[0].snippet == "snippet one"


def test_build_web_search_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    ws = build_web_search()
    assert ws.search("anything") == []   # keyless -> gap-fill skipped
