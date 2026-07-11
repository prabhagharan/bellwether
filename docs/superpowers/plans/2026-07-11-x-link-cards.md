# Enrich X Tweets with Link-Card Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold each X link card's title + description (from the API's `entities.urls`) into the tweet text so link-only tweets become analyzable, with no extra API call.

**Architecture:** A single-file change to `src/bellwether/connectors/x.py` — request `entities` in the fetch, and in `_parse_x_timeline` append each URL card's `title — description` to `RawItem.text`.

**Tech Stack:** Python, X API v2, pytest (pure unit tests — no network via crafted payloads + a monkeypatched `urlopen`).

## Global Constraints

- Change is confined to `src/bellwether/connectors/x.py` and its test `tests/connectors/test_x.py`. No other files.
- Fetch URL fields: `tweet.fields=created_at` → `tweet.fields=created_at,entities` (same call/tier).
- In `_parse_x_timeline`, build each `RawItem.text` as `tweet_text` followed by one paragraph per URL entity **that has a title and/or description**:
  - both present → `"<title> — <description>"` (em dash `—`, space-padded);
  - only one present → that one;
  - neither → the URL entity is **skipped**.
  - multiple cards → one paragraph each, in entity order, joined to the text with `"\n\n"`.
  - no `entities`/`urls` → text is the raw tweet text (unchanged from today).
- Keep the original tweet text unchanged (including the raw `t.co` URL); cards are appended.
- `XConnector.fetch` keeps its existing behavior (no key → `[]`; request exception → `[]`).
- Live-verify against `@realDonaldTrump` before merge.

---

### Task 1: Fold link-card title/description into X tweet text

**Files:**
- Modify: `src/bellwether/connectors/x.py` (fetch URL fields; `_parse_x_timeline`)
- Test: `tests/connectors/test_x.py` (add cases)

**Interfaces:**
- Produces: `_parse_x_timeline(payload: dict, handle: str) -> list[RawItem]` now folds each URL card's `title`/`description` into `RawItem.text`; `XConnector.fetch` requests `entities`.

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/connectors/test_x.py`. First extend the import line at the top from:

```python
from bellwether.connectors.x import _parse_x_timeline, XConnector
```

to:

```python
import json
import urllib.request
from bellwether.connectors.x import _parse_x_timeline, XConnector
```

Then append:

```python
def test_parse_folds_link_card_title_and_description():
    payload = {"data": [{
        "id": "3", "text": "https://t.co/abc", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"title": "The White House",
                               "description": "President Trump Delivers Remarks"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "realDonaldTrump")
    assert item.text == "https://t.co/abc\n\nThe White House — President Trump Delivers Remarks"


def test_parse_card_with_only_one_of_title_or_description():
    payload = {"data": [{
        "id": "4", "text": "vid", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"description": "just a description"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "vid\n\njust a description"


def test_parse_skips_url_entity_without_title_or_description():
    payload = {"data": [{
        "id": "5", "text": "plain https://t.co/x", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"expanded_url": "https://example.com"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "plain https://t.co/x"


def test_parse_folds_multiple_cards():
    payload = {"data": [{
        "id": "6", "text": "two links", "created_at": "2026-07-05T03:00:00.000Z",
        "entities": {"urls": [{"title": "A", "description": "aa"},
                              {"title": "B", "description": "bb"}]},
    }]}
    (item,) = _parse_x_timeline(payload, "h")
    assert item.text == "two links\n\nA — aa\n\nB — bb"


def test_fetch_requests_entities_and_folds_cards(monkeypatch):
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"data": [{
                "id": "9", "text": "https://t.co/z", "created_at": "2026-07-05T03:00:00.000Z",
                "entities": {"urls": [{"title": "T", "description": "D"}]}}]}).encode()

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    items = XConnector("h", api_key="k").fetch()
    assert "tweet.fields=created_at,entities" in captured["url"]
    assert items[0].text == "https://t.co/z\n\nT — D"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/connectors/test_x.py -q`
Expected: the 5 new tests FAIL — the folded-card assertions fail (text is still the bare tweet text) and `test_fetch_requests_entities_and_folds_cards` fails on the `tweet.fields=created_at,entities` assertion. (`test_parse_x_timeline` and `test_connector_disabled_without_key` still pass.)

- [ ] **Step 3: Implement the card folding + request entities**

In `src/bellwether/connectors/x.py`, add a helper above `_parse_x_timeline`:

```python
def _card_lines(entities: dict | None) -> list[str]:
    """Fold X url-entity link cards into extra text lines: '<title> — <description>'
    (or whichever of the two is present). URL entities with neither are skipped."""
    lines: list[str] = []
    for u in (entities or {}).get("urls", []) or []:
        title = (u.get("title") or "").strip()
        desc = (u.get("description") or "").strip()
        if title and desc:
            lines.append(f"{title} — {desc}")
        elif title or desc:
            lines.append(title or desc)
    return lines
```

Then change `_parse_x_timeline`'s `RawItem` construction from:

```python
        items.append(RawItem(external_id=str(tid), text=t.get("text", ""),
                             url=f"https://x.com/{handle}/status/{tid}", published_at=published_at))
```

to:

```python
        text = t.get("text", "")
        cards = _card_lines(t.get("entities"))
        if cards:
            text = "\n\n".join([text, *cards])
        items.append(RawItem(external_id=str(tid), text=text,
                             url=f"https://x.com/{handle}/status/{tid}", published_at=published_at))
```

And in `XConnector.fetch`, change the request URL's fields from `tweet.fields=created_at` to `tweet.fields=created_at,entities`:

```python
        url = (f"https://api.twitter.com/2/tweets/search/recent"
               f"?query=from:{self.handle}&tweet.fields=created_at,entities&max_results=20")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/connectors/test_x.py -q`
Expected: PASS (all — the 2 original tests plus the 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/bellwether/connectors/x.py tests/connectors/test_x.py
git commit -m "feat: fold X link-card title/description into tweet text"
```

---

## Live verification (before merge, not a task)

Fetch a real handle and confirm a link-only tweet now carries the card text:

```bash
export $(grep -E '^X_API_KEY=' .env | xargs)
.venv/bin/python -c "
from bellwether.connectors.x import XConnector
import os
for it in XConnector('realDonaldTrump', os.environ.get('X_API_KEY')).fetch()[:5]:
    print(repr(it.text[:120]))
"
```

Expected: the link-only tweet's text now includes something like
`'https://t.co/… \n\nThe White House — President Trump Delivers Remarks at the Salute to America Celebration'`
(exact tweet varies by what's recent). Optionally re-run detect on that statement to confirm it's no longer trivially `irrelevant`.

## Self-review notes

- **Spec coverage:** request `entities` (Step 3 fetch URL + Step 1 fetch test) ✓; fold title+desc always (Step 3 `_card_lines` + Step 1 test) ✓; one-present branch ✓; skip-neither ✓; multiple cards ✓; no-entities unchanged (existing `test_parse_x_timeline` + the skip logic) ✓; keep raw tweet text ✓; live verification ✓; single-file scope ✓.
- **Placeholder scan:** none — every step carries concrete code/commands.
- **Consistency:** `_card_lines(entities)` defined and used in the same task; the `"\n\n".join([text, *cards])` join format matches the exact strings the tests assert (`"...\n\n<title> — <description>"`). Em dash `—` is identical in the impl and the tests.
