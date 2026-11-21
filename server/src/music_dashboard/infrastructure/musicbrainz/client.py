"""MusicBrainz genre lookup adapter (fallback path)."""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
)

from .transport import (
    HttpRequest,
    HttpTransport,
    RateLimiter,
    parse_retry_after,
    rate_limit_exhausted_delay,
    shared_musicbrainz_limiter,
    urllib_transport,
)

_MBID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PATHS = {"recording": "recording", "release_group": "release-group", "artist": "artist"}
DEFAULT_RETRY_AFTER = 60.0


@dataclass(frozen=True)
class MusicBrainzConfig:
    user_agent: str  # e.g. "music-dashboard/0.1 ( contact@example.org )"; required
    base_url: str = "https://musicbrainz.org/ws/2"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        contact = re.search(r"\(([^)]+)\)", self.user_agent)
        if (
            not re.match(r"\S+/\S+\s", self.user_agent)
            or contact is None
            or not any(marker in contact[1] for marker in ("@", "https://", "http://"))
        ):
            raise ValueError(
                "user_agent must identify product/version and include contact in parentheses"
            )

    def __repr__(self) -> str:
        return "MusicBrainzConfig(...)"


class MusicBrainzClient:
    def __init__(
        self,
        config: MusicBrainzConfig,
        transport: HttpTransport = urllib_transport,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._limiter = limiter or shared_musicbrainz_limiter()

    def genres(self, entity_type: str, mbid: str) -> dict[str, Any]:
        """Return {"entity_type","mbid","genres":[{"mbid","name","count"}]}.

        404 -> empty genres (caller negative-caches). Raises MetadataTransientError
        (with retry_after seconds) for 429/503/5xx/network; MetadataPermanentError else.
        """
        path = _PATHS.get(entity_type)
        if path is None:
            raise MetadataPermanentError("unsupported entity type")
        if not _MBID.match(mbid):
            raise MetadataPermanentError("invalid mbid")
        url = f"{self._config.base_url}/{path}/{quote(mbid.lower())}?inc=genres&fmt=json"
        request = HttpRequest(
            "GET",
            url,
            {"User-Agent": self._config.user_agent, "Accept": "application/json"},
            None,
            self._config.timeout,
        )
        self._limiter.acquire()
        resp = self._transport(request)
        delay = rate_limit_exhausted_delay(resp.headers)
        retry_after = parse_retry_after(resp.headers)
        if retry_after is not None:
            delay = max(delay or 0, retry_after)
        if delay is not None:
            self._limiter.defer(delay)
        if resp.status == 404:
            return {"entity_type": entity_type, "mbid": mbid.lower(), "genres": []}
        if resp.status in (429, 503) or resp.status >= 500:
            wait = retry_after if retry_after is not None else (delay or DEFAULT_RETRY_AFTER)
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
        raw = data.get("genres")
        if raw is not None and not isinstance(raw, list):
            raise MetadataTransientError("musicbrainz malformed genres", DEFAULT_RETRY_AFTER)
        genres: list[dict[str, Any]] = []
        for g in raw or []:
            count = g.get("count") if isinstance(g, dict) else None
            if (
                not isinstance(g, dict)
                or isinstance(count, bool)
                or not (count is None or isinstance(count, int))
            ):
                raise MetadataTransientError("musicbrainz malformed genre", DEFAULT_RETRY_AFTER)
            if g.get("id"):
                genres.append({"mbid": g["id"], "name": g.get("name"), "count": count or 0})
        result: dict[str, Any] = {
            "entity_type": entity_type,
            "mbid": mbid.lower(),
            "genres": genres,
        }
        if delay is not None and delay > 0:
            result["_retry_after"] = delay
        if entity_type == "artist":
            raw_area = data.get("area")
            area: dict[str, Any] = raw_area if isinstance(raw_area, dict) else {}
            result["country"] = data.get("country")
            result["area_iso_3166_1_codes"] = area.get("iso-3166-1-codes") or []
        return result
