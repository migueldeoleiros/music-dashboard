"""Exact-MBID title lookup for releases / release groups (worker only; no cover data).

Verifies the returned id equals the requested MBID and the payload has the requested entity's
shape before a title is trusted. 404 -> not found. Never searches by name."""

import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
    TitleDeadlineError,
)

from .client import DEFAULT_RETRY_AFTER, MusicBrainzConfig
from .transport import (
    HttpRequest,
    HttpTransport,
    RateLimiter,
    bounded_transport,
    parse_retry_after,
    rate_limit_exhausted_delay,
    shared_musicbrainz_limiter,
)

_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PATHS = {"release": "release", "release_group": "release-group"}
MAX_TITLE = 1000
MIN_REQUEST_SECONDS = 1.0
MAX_DELAY_SECONDS = 86400.0  # title-only ceiling for any server-supplied delay (1 day)
MAX_BODY_BYTES = 256 * 1024  # a title payload is a few KB; larger is bogus


def _header_delay(headers: Any) -> float | None:
    """Longest of Retry-After and the exhausted-rate-limit reset, clamped to MAX_DELAY_SECONDS.
    Unparseable/non-finite -> ignored. Title-only: shared parsers are unchanged."""
    delays = []
    for parse in (rate_limit_exhausted_delay, parse_retry_after):
        try:
            value = parse(headers)
        except (OverflowError, OSError, ValueError):
            value = None
        if value is not None:
            delays.append(min(value, MAX_DELAY_SECONDS))
    return max(delays) if delays else None


class MusicBrainzTitleClient:
    def __init__(
        self,
        config: MusicBrainzConfig,
        transport: HttpTransport = bounded_transport,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._limiter = limiter or shared_musicbrainz_limiter()

    def title(
        self, entity_type: str, mbid: str, timeout: float | None = None
    ) -> dict[str, Any] | None:
        """{"title": str, "release_group_mbid": str|None}, or None when MB has no such entity.

        `timeout` (seconds left in the run deadline) bounds limiter wait + request: too little left
        raises TitleDeadlineError before any request. urllib's timeout is a per-socket-operation
        limit; the default bounded_transport runs the request in a spawned child and bounds the
        caller's wait by `timeout` across DNS/connect/headers/body (killing the child on expiry),
        and caps the body at MAX_BODY_BYTES. Residuals: the post-ready local pipe read and the
        1s kill/join cleanup are not time-bounded. Server delays are clamped to 1 day here only.

        MetadataTransientError for 429/5xx/network/invalid body; MetadataPermanentError when
        the response is not the requested entity (id/type mismatch, bad title)."""
        path = _PATHS.get(entity_type)
        if path is None:
            raise MetadataPermanentError("unsupported entity type")
        mbid = mbid.lower()
        if not _MBID.match(mbid):
            raise MetadataPermanentError("invalid mbid")
        inc = "&inc=release-groups" if entity_type == "release" else ""
        request = HttpRequest(
            "GET",
            f"{self._config.base_url}/{path}/{quote(mbid)}?fmt=json{inc}",
            {"User-Agent": self._config.user_agent, "Accept": "application/json"},
            None,
            min(self._config.timeout, timeout) if timeout else self._config.timeout,
            MAX_BODY_BYTES,
        )
        if timeout is not None:
            wait = self._limiter.pending_wait()
            budget = timeout - wait
            if budget < MIN_REQUEST_SECONDS:  # decline rather than overrun the run deadline
                raise TitleDeadlineError
            request = replace(request, timeout=min(request.timeout, budget))
        self._limiter.acquire()
        resp = self._transport(request)
        delay = _header_delay(resp.headers)  # max(Retry-After, rate-limit reset), clamped
        if delay is not None:
            self._limiter.defer(delay)
        if resp.status == 404:
            return None
        if resp.status in (429, 503) or resp.status >= 500:
            wait = delay if delay is not None else DEFAULT_RETRY_AFTER  # same delay as limiter
            self._limiter.defer(wait)
            raise MetadataTransientError(f"musicbrainz http {resp.status}", wait)
        if resp.status != 200:
            raise MetadataPermanentError(
                f"musicbrainz http {resp.status}", source_wide=resp.status in (401, 403)
            )
        try:
            data = json.loads(resp.body)
        except ValueError:
            raise MetadataTransientError("musicbrainz invalid json", DEFAULT_RETRY_AFTER) from None
        if not isinstance(data, dict):
            raise MetadataTransientError("musicbrainz unexpected body", DEFAULT_RETRY_AFTER)
        if str(data.get("id", "")).lower() != mbid:
            raise MetadataPermanentError("musicbrainz id mismatch")
        title = data.get("title")
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > MAX_TITLE
            or "\x00" in title
        ):
            raise MetadataPermanentError("musicbrainz invalid title")
        rg_mbid: str | None = None
        if entity_type == "release":
            group = data.get("release-group")
            gid = str(group.get("id", "")).lower() if isinstance(group, dict) else ""
            if not _MBID.match(gid):
                raise MetadataPermanentError("musicbrainz not a release")
            rg_mbid = gid
        elif "release-group" in data or not (
            "primary-type" in data or "first-release-date" in data or "secondary-types" in data
        ):
            raise MetadataPermanentError("musicbrainz not a release group")
        return {"title": title, "release_group_mbid": rg_mbid}
