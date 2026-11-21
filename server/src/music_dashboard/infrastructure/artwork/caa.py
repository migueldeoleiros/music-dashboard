"""Cover Art Archive: verify with HEAD (following redirects), store the ORIGINAL URL."""

from music_dashboard.domain.artwork.errors import ArtworkTransientError
from music_dashboard.infrastructure.artwork.http import DEFAULT_RETRY_AFTER, fetch
from music_dashboard.infrastructure.artwork.transport import (
    HttpTransport,
    unauthenticated_transport,
)
from music_dashboard.infrastructure.artwork.urls import MBID, validate_image_url
from music_dashboard.infrastructure.musicbrainz.transport import RateLimiter

KINDS = ("release-group", "release")


class CoverArtArchiveClient:
    def __init__(
        self,
        user_agent: str,
        transport: HttpTransport = unauthenticated_transport,
        limiter: RateLimiter | None = None,
        base_url: str = "https://coverartarchive.org",
    ) -> None:
        self._headers = {"User-Agent": user_agent}
        self._transport = transport
        self._limiter = limiter or RateLimiter(0.25)
        self._base = base_url

    def front_url(self, kind: str, mbid: str) -> str | None:
        """The coverartarchive.org front URL if an image exists, else None (404)."""
        if kind not in KINDS or not MBID.match(mbid):
            return None
        url = f"{self._base}/{kind}/{mbid}/front"
        resp = fetch(self._transport, self._limiter, "HEAD", url, self._headers)
        if resp.status == 404:
            return None
        if resp.status != 200:
            if resp.status in (408, 425):
                raise ArtworkTransientError(f"caa http {resp.status}", DEFAULT_RETRY_AFTER)
            return None
        kind_header = resp.headers.get("content-type", "image/")
        if not kind_header.lower().startswith("image/"):
            return None
        return validate_image_url(url)  # the original URL, never the redirected storage host
