"""MusicBrainz lookups needed for artwork (separate from the genre client).

Exact-MBID lookups only: release (barcode, url-rels, release-group), recording (isrcs,
releases), artist (url-rels + aliases; the alias list rides on the same request).
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
MAX_RELEASES = 5  # bounded CAA probes per recording
MAX_ALIASES = 8
# Only aliases that are real alternative artist names; 'Legal name' and 'Search hint' are not.
TRUSTED_ALIAS_TYPES = frozenset({None, "Artist name"})


@dataclass(frozen=True)
class ReleaseInfo:
    barcode: str | None
    deezer_album_id: str | None
    release_group_mbid: str | None = None  # explicit MB release -> release-group relation


@dataclass(frozen=True)
class RecordingInfo:
    isrcs: list[str]
    release_mbids: list[str]  # releases MB lists for this exact recording (bounded)


@dataclass(frozen=True)
class ArtistLinks:
    deezer_artist_id: str | None
    wikidata_qid: str | None
    name: str | None = None  # MusicBrainz canonical artist name
    aliases: tuple[str, ...] = ()  # trusted aliases only (see _aliases)


def _urls(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rel in data.get("relations") or []:
        resource = ((rel or {}).get("url") or {}).get("resource") if isinstance(rel, dict) else None
        if isinstance(resource, str):
            out.append(resource)
    return out


def _aliases(data: dict[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for alias in data.get("aliases") or []:
        if not isinstance(alias, dict) or alias.get("type") not in TRUSTED_ALIAS_TYPES:
            continue
        name = alias.get("name")
        if isinstance(name, str) and name.strip() and name not in out:
            out.append(name)
    return tuple(out[:MAX_ALIASES])


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
        data = self._lookup("release", "url-rels+release-groups", mbid)
        if data is None:
            return None
        barcode = data.get("barcode")
        group = data.get("release-group")
        group_id = group.get("id") if isinstance(group, dict) else None
        return ReleaseInfo(
            barcode if isinstance(barcode, str) and BARCODE.match(barcode) else None,
            _first(_DEEZER_ALBUM, _urls(data)),
            group_id.lower()
            if isinstance(group_id, str) and MBID.match(group_id.lower())
            else None,
        )

    def recording(self, mbid: str) -> RecordingInfo:
        data = self._lookup("recording", "isrcs+releases", mbid) or {}
        isrcs = [i for i in data.get("isrcs") or [] if isinstance(i, str) and ISRC.match(i)]
        releases: list[str] = []
        for rel in data.get("releases") or []:
            rid = rel.get("id") if isinstance(rel, dict) else None
            rid = rid.lower() if isinstance(rid, str) else ""
            if MBID.match(rid) and rid not in releases:
                releases.append(rid)
        return RecordingInfo(isrcs[:MAX_ISRCS], releases[:MAX_RELEASES])

    def artist_links(self, mbid: str) -> ArtistLinks:
        data = self._lookup("artist", "url-rels+aliases", mbid) or {}
        urls = _urls(data)
        name = data.get("name")
        return ArtistLinks(
            _first(_DEEZER_ARTIST, urls),
            _first(_WIKIDATA, urls),
            name if isinstance(name, str) and name.strip() else None,
            _aliases(data),
        )
