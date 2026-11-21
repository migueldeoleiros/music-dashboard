"""Wikidata P2722 (Deezer artist ID) via Special:EntityData JSON of an exact Q-id."""

from typing import Any

from music_dashboard.domain.artwork.errors import ArtworkTransientError
from music_dashboard.infrastructure.artwork.http import DEFAULT_RETRY_AFTER, fetch, json_body
from music_dashboard.infrastructure.artwork.transport import (
    HttpTransport,
    unauthenticated_transport,
)
from music_dashboard.infrastructure.artwork.urls import NUMERIC_ID, QID
from music_dashboard.infrastructure.musicbrainz.transport import RateLimiter


class WikidataClient:
    def __init__(
        self,
        user_agent: str,
        transport: HttpTransport = unauthenticated_transport,
        limiter: RateLimiter | None = None,
        base_url: str = "https://www.wikidata.org",
    ) -> None:
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._transport = transport
        self._limiter = limiter or RateLimiter(1.0)
        self._base = base_url

    def deezer_artist_id(self, qid: str) -> str | None:
        """First entity in the response (redirects are followed), first non-deprecated P2722."""
        if not QID.match(qid):
            return None
        url = f"{self._base}/wiki/Special:EntityData/{qid}.json"
        resp = fetch(self._transport, self._limiter, "GET", url, self._headers)
        if resp.status in (404, 410):
            return None
        if resp.status != 200:
            raise ArtworkTransientError(f"wikidata http {resp.status}", DEFAULT_RETRY_AFTER)
        data = json_body(resp)
        entities = data.get("entities") if isinstance(data, dict) else None
        if not isinstance(entities, dict) or not entities:
            return None
        entity: Any = next(iter(entities.values()))
        claims = entity.get("claims", {}).get("P2722", []) if isinstance(entity, dict) else []
        for claim in claims if isinstance(claims, list) else []:
            if not isinstance(claim, dict) or claim.get("rank") == "deprecated":
                continue
            value = (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")
            if isinstance(value, str) and NUMERIC_ID.match(value):
                return value
        return None
