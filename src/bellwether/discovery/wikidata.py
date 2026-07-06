import json
from urllib.parse import urlencode
from bellwether.discovery.contracts import (
    WikidataEntity, WikidataClaims, WikidataClient, HttpClient, DiscoveryError,
)
from bellwether.discovery.http import build_http

_API = "https://www.wikidata.org/w/api.php"


def _parse_search(payload: dict) -> list[WikidataEntity]:
    return [
        WikidataEntity(qid=r["id"], label=r.get("label", ""), description=r.get("description", ""))
        for r in payload.get("search", [])
    ]


def _first(claims: dict, prop: str) -> str | None:
    entries = claims.get(prop)
    if not entries:
        return None
    try:
        return entries[0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_claims(payload: dict) -> WikidataClaims:
    entity = next(iter(payload.get("entities", {}).values()), {})
    claims = entity.get("claims", {})
    aliases = [a["value"] for a in entity.get("aliases", {}).get("en", [])]
    return WikidataClaims(
        website=_first(claims, "P856"),
        x_username=_first(claims, "P2002"),
        youtube_channel=_first(claims, "P2397"),
        aliases=aliases,
    )


class WikidataAdapter:
    def __init__(self, http: HttpClient):
        self._http = http

    def _get_json(self, params: dict) -> dict:
        res = self._http.get(f"{_API}?{urlencode(params)}")
        if not res.ok or res.text is None:
            raise DiscoveryError("wikidata request failed")
        try:
            return json.loads(res.text)
        except json.JSONDecodeError as exc:
            raise DiscoveryError("wikidata returned invalid JSON") from exc

    def search(self, name: str) -> list[WikidataEntity]:
        return _parse_search(self._get_json({
            "action": "wbsearchentities", "search": name, "language": "en",
            "format": "json", "limit": "5",
        }))

    def claims(self, qid: str) -> WikidataClaims:
        return _parse_claims(self._get_json({
            "action": "wbgetentities", "ids": qid, "props": "claims|aliases",
            "languages": "en", "format": "json",
        }))


def build_wikidata() -> WikidataClient:
    return WikidataAdapter(build_http())
