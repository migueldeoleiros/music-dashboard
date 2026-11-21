"""Rate-limited request helper shared by the artwork adapters."""

import json
from collections.abc import Mapping
from typing import Any, Protocol

from music_dashboard.domain.artwork.errors import ArtworkTransientError
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
    parse_retry_after,
    rate_limit_exhausted_delay,
)

DEFAULT_RETRY_AFTER = 60.0


class Limiter(Protocol):
    def acquire(self) -> None: ...
    def defer(self, seconds: float) -> None: ...


def fetch(
    transport: HttpTransport,
    limiter: Limiter,
    method: str,
    url: str,
    headers: Mapping[str, str],
    timeout: float = 20.0,
) -> HttpResponse:
    """One limited request. 429/503/5xx raise ArtworkTransientError and defer the limiter."""
    limiter.acquire()
    resp = transport(HttpRequest(method, url, headers, None, timeout))
    delay = rate_limit_exhausted_delay(resp.headers)
    retry_after = parse_retry_after(resp.headers)
    if retry_after is not None:
        delay = max(delay or 0, retry_after)
    if delay is not None:
        limiter.defer(delay)
    if resp.status in (429, 503) or resp.status >= 500:
        wait = retry_after if retry_after is not None else (delay or DEFAULT_RETRY_AFTER)
        limiter.defer(wait)
        raise ArtworkTransientError(f"http {resp.status}", wait)
    return resp


def json_body(resp: HttpResponse) -> Any:
    try:
        return json.loads(resp.body)
    except ValueError:
        raise ArtworkTransientError("invalid json", DEFAULT_RETRY_AFTER) from None
