"""MusicBrainz lookups needed for artwork (separate from the genre client).

Exact-MBID lookups only: release (barcode + url-rels), recording (isrcs), artist (url-rels).
Shares the process-wide 1 req/s MusicBrainz limiter with the enrichment client and honors the
same 429/503/Retry-After handling. Unauthenticated: no credentials of any kind.
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from music_dashboard.domain.artwork.errors import ArtworkTransientError
from music_dashboard.infrastructure.artwork.http import (
    DEFAULT_RETRY_AFTER,
    Limiter,
    fetch,
    json_body,
)
from music_dashboard.infrastructure.artwork.transport import (
    HttpTransport,
    unauthenticated_transport,
)
from music_dashboard.infrastructure.artwork.urls import BARCODE, ISRC, MBID
from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
from music_dashboard.infrastructure.musicbrainz.transport import shared_musicbrainz_limiter

_DEEZER = r"^https?://(?:www\.)?deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?%s/([0-9]+)/?$"
_DEEZER_ALBUM = re.compile(_DEEZER % "album")
_DEEZER_ARTIST = re.compile(_DEEZER % "artist")
_WIKIDATA = re.compile(r"^https?://(?:www\.)?wikidata\.org/(?:wiki|entity)/(Q[0-9]+)/?$")
MAX_ISRCS = 3


@dataclass(frozen=True)
class ReleaseInfo:
    barcode: str | None
    deezer_album_id: str | None


@dataclass(frozen=True)
class ArtistLinks:
    deezer_artist_id: str | None
    wikidata_qid: str | None


def _urls(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rel in data.get("relations") or []:
        resource = ((rel or {}).get("url") or {}).get("resource") if isinstance(rel, dict) else None
        if isinstance(resource, str):
            out.append(resource)
    return out


def _first(pattern: re.Pattern[str], urls: list[str]) -> str | None:
    for url in urls:
        match = pattern.match(url)
        if match:
            return match.group(1)
    return None


class MusicBrainzArtworkLookups:
    def __init__(
        self,
        config: MusicBrainzConfig,
        transport: HttpTransport = unauthenticated_transport,
        limiter: Limiter | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._limiter: Limiter = limiter or shared_musicbrainz_limiter()
        self._headers = {"User-Agent": config.user_agent, "Accept": "application/json"}

    def _lookup(self, path: str, inc: str, mbid: str) -> dict[str, Any] | None:
        mbid = mbid.lower()
        if not MBID.match(mbid):
            return None
        url = f"{self._config.base_url}/{path}/{quote(mbid)}?inc={inc}&fmt=json"
        resp = fetch(
            self._transport, self._limiter, "GET", url, self._headers, self._config.timeout
        )
        if resp.status == 404:
            return None
        if resp.status != 200:
            raise ArtworkTransientError(f"musicbrainz http {resp.status}", DEFAULT_RETRY_AFTER)
        data = json_body(resp)
        if not isinstance(data, dict):
            raise ArtworkTransientError("musicbrainz unexpected body", DEFAULT_RETRY_AFTER)
        return data

    def release(self, mbid: str) -> ReleaseInfo | None:
        data = self._lookup("release", "url-rels", mbid)
        if data is None:
            return None
        barcode = data.get("barcode")
        return ReleaseInfo(
            barcode if isinstance(barcode, str) and BARCODE.match(barcode) else None,
            _first(_DEEZER_ALBUM, _urls(data)),
        )

    def recording_isrcs(self, mbid: str) -> list[str]:
        data = self._lookup("recording", "isrcs", mbid)
        raw = (data or {}).get("isrcs") or []
        isrcs = [i for i in raw if isinstance(i, str) and ISRC.match(i)]
        return isrcs[:MAX_ISRCS]

    def artist_links(self, mbid: str) -> ArtistLinks:
        data = self._lookup("artist", "url-rels", mbid)
        urls = _urls(data or {})
        return ArtistLinks(_first(_DEEZER_ARTIST, urls), _first(_WIKIDATA, urls))
