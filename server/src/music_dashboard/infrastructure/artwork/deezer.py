"""Deezer adapter: exact UPC / ISRC / id lookups plus bounded name SEARCH (fallback only).
Stores hash URLs only.

Search is the only place a name reaches a request URL: it is sanitised (control characters and
quotes removed, length capped), percent-encoded into the `q` parameter of a fixed host/path, and
answered with at most MAX_RESULTS candidates. Search responses are never trusted: matching
(exact normalized names, ambiguity and credit-conflict rejection) happens in the domain layer.
Search shares the same token bucket as every other Deezer request; no credentials are sent.

Deezer replies HTTP 200 with {"error": {...}} for most failures: code 800 (no data) is a miss,
code 4 (quota) raises ArtworkQuotaError, 700 (busy) is transient. Images are never downloaded.
"""

import re
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from music_dashboard.domain.artwork.errors import ArtworkQuotaError, ArtworkTransientError
from music_dashboard.domain.artwork.matching import (
    MAX_NAME,
    AlbumCandidate,
    ArtistCandidate,
    TrackCandidate,
)
from music_dashboard.infrastructure.artwork.http import DEFAULT_RETRY_AFTER, fetch, json_body
from music_dashboard.infrastructure.artwork.transport import (
    HttpTransport,
    unauthenticated_transport,
)
from music_dashboard.infrastructure.artwork.urls import (
    BARCODE,
    ISRC,
    NUMERIC_ID,
    validate_image_url,
)

EMPTY_HASH = "d41d8cd98f00b204e9800998ecf8427e"  # md5 of nothing: Deezer's "no picture"
_HASH_URL = re.compile(r"^https://[^/]+/images/(?:cover|artist)/([0-9a-f]{32})/")
QUOTA_CODE = 4
NO_DATA_CODE = 800
BUSY_CODE = 700
MAX_RESULTS = 25
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\"\\]+")


def search_term(value: str | None) -> str | None:
    """Name safe to embed in a quoted Deezer search term, or None when nothing usable remains."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(_CONTROL.sub(" ", value).split())[:MAX_NAME].strip()
    return cleaned or None


class TokenBucket:
    """~rate req/s with a small burst; far below Deezer's 50 requests / 5 s."""

    def __init__(
        self,
        rate: float = 5.0,
        capacity: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate, self._capacity = rate, capacity
        self._clock, self._sleep = clock, sleep
        self._tokens = capacity
        self._stamp = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._stamp) * self._rate)
        self._stamp = now

    def acquire(self) -> None:
        with self._lock:
            self._refill()
            if self._tokens < 1:
                self._sleep((1 - self._tokens) / self._rate)
                self._refill()
            self._tokens -= 1

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._refill()
            self._tokens = min(self._tokens, -seconds * self._rate)


def usable_image(url: object) -> str | None:
    """Allowlisted https URL with a real (non-empty) image hash, else None."""
    valid = validate_image_url(url)
    match = _HASH_URL.match(valid) if valid else None
    if valid is None or match is None or match.group(1) == EMPTY_HASH:
        return None
    return valid


class DeezerClient:
    def __init__(
        self,
        user_agent: str,
        transport: HttpTransport = unauthenticated_transport,
        bucket: TokenBucket | None = None,
        base_url: str = "https://api.deezer.com",
    ) -> None:
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._transport = transport
        self._bucket = bucket or TokenBucket()
        self._base = base_url

    def _get(self, path: str) -> dict[str, Any] | None:
        resp = fetch(self._transport, self._bucket, "GET", f"{self._base}/{path}", self._headers)
        if resp.status in (400, 404):
            return None
        if resp.status != 200:
            raise ArtworkTransientError(f"deezer http {resp.status}", DEFAULT_RETRY_AFTER)
        data = json_body(resp)
        if not isinstance(data, dict):
            raise ArtworkTransientError("deezer unexpected body", DEFAULT_RETRY_AFTER)
        error = data.get("error")
        if error is None:
            return data
        code = error.get("code") if isinstance(error, dict) else None
        if code == QUOTA_CODE:
            raise ArtworkQuotaError("deezer quota", 10.0)
        if code == BUSY_CODE:
            raise ArtworkTransientError("deezer busy", DEFAULT_RETRY_AFTER)
        return None  # 800 no data and other entity-level errors: miss

    @staticmethod
    def _first(data: dict[str, Any] | None, *fields: str) -> str | None:
        for field in fields:
            url = usable_image((data or {}).get(field))
            if url:
                return url
        return None

    def album_by_upc(self, barcode: str) -> str | None:
        if not BARCODE.match(barcode):
            return None
        return self._first(self._get(f"album/upc:{barcode}"), "cover_xl", "cover_big")

    def album(self, deezer_id: str) -> str | None:
        if not NUMERIC_ID.match(deezer_id):
            return None
        return self._first(self._get(f"album/{deezer_id}"), "cover_xl", "cover_big")

    def track_album_cover(self, isrc: str) -> str | None:
        """Album cover of the track found by exact ISRC."""
        if not ISRC.match(isrc):
            return None
        data = self._get(f"track/isrc:{isrc}")
        album = (data or {}).get("album")
        return self._first(album if isinstance(album, dict) else None, "cover_xl", "cover_big")

    def artist_picture(self, deezer_id: str) -> str | None:
        if not NUMERIC_ID.match(deezer_id):
            return None
        return self._first(self._get(f"artist/{deezer_id}"), "picture_xl", "picture_big")

    # --- name search (fallback; candidates only, never trusted) ------------------------------
    def _search(self, kind: str, query: str) -> list[dict[str, Any]]:
        params = urlencode({"q": query, "limit": MAX_RESULTS})
        rows = (self._get(f"search/{kind}?{params}") or {}).get("data")
        if not isinstance(rows, list):
            return []
        return [r for r in rows[:MAX_RESULTS] if isinstance(r, dict)]

    @staticmethod
    def _id(value: object) -> str | None:
        text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
        return text if isinstance(text, str) and NUMERIC_ID.match(text) else None

    @staticmethod
    def _name(row: object, field: str) -> str | None:
        value = row.get(field) if isinstance(row, dict) else None
        return value if isinstance(value, str) and value.strip() else None

    def search_artists(self, name: str) -> list[ArtistCandidate]:
        term = search_term(name)
        if term is None:
            return []
        out: list[ArtistCandidate] = []
        for row in self._search("artist", f'artist:"{term}"'):
            ident, label = self._id(row.get("id")), self._name(row, "name")
            if ident and label:
                out.append(
                    ArtistCandidate(ident, label, self._first(row, "picture_xl", "picture_big"))
                )
        return out

    def search_albums(self, title: str, artist: str | None = None) -> list[AlbumCandidate]:
        term, artist_term = search_term(title), search_term(artist)
        if term is None:
            return []
        query = f'album:"{term}"' + (f' artist:"{artist_term}"' if artist_term else "")
        out: list[AlbumCandidate] = []
        for row in self._search("album", query):
            ident, label = self._id(row.get("id")), self._name(row, "title")
            who = self._name(row.get("artist"), "name")
            if ident and label and who:
                out.append(
                    AlbumCandidate(ident, label, who, self._first(row, "cover_xl", "cover_big"))
                )
        return out

    def search_tracks(self, title: str, artist: str) -> list[TrackCandidate]:
        term, artist_term = search_term(title), search_term(artist)
        if term is None or artist_term is None:
            return []
        out: list[TrackCandidate] = []
        for row in self._search("track", f'track:"{term}" artist:"{artist_term}"'):
            ident, label = self._id(row.get("id")), self._name(row, "title")
            who = self._name(row.get("artist"), "name")
            album = row.get("album")
            album_title = self._name(album, "title")
            if ident and label and who and album_title:
                cover = self._first(album, "cover_xl", "cover_big")
                out.append(TrackCandidate(ident, label, who, album_title, cover))
        return out
