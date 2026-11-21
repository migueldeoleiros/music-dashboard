"""Minimal injectable HTTP transport (stdlib urllib) and shared rate limiter."""

import math
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from http.client import HTTPException

from music_dashboard.domain.metadata.errors import MetadataTransientError


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = 30.0

    def __repr__(self) -> str:  # never leak url/headers/body
        return f"HttpRequest(method={self.method!r})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


HttpTransport = Callable[[HttpRequest], HttpResponse]


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """Production transport. Returns HTTP error statuses as responses; network
    failures become MetadataTransientError without URL details."""
    req = urllib.request.Request(  # noqa: S310 - scheme fixed by adapters
        request.url, data=request.body, headers=dict(request.headers), method=request.method
    )
    try:
        with urllib.request.urlopen(req, timeout=request.timeout) as resp:  # noqa: S310
            return HttpResponse(
                resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
            )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()}, body)
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
        raise MetadataTransientError("network error") from None


class RateLimiter:
    """Spaces calls by min_interval; clock/sleep injectable for fake-clock tests."""

    def __init__(
        self,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            wait = self._next_allowed - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed) + self._interval

    def defer(self, seconds: float) -> None:
        """Push next allowed call out (server rate headers / Retry-After)."""
        with self._lock:
            self._next_allowed = max(self._next_allowed, self._clock() + seconds)


_shared = RateLimiter(1.0)


def shared_musicbrainz_limiter() -> RateLimiter:
    """Process-wide limiter shared by all MusicBrainzClient instances."""
    return _shared


def parse_retry_after(headers: Mapping[str, str], *, now: float | None = None) -> float | None:
    """Retry-After supports both delta seconds and an RFC HTTP date."""
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        try:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def rate_limit_exhausted_delay(
    headers: Mapping[str, str],
    *,
    now: float | None = None,
) -> float | None:
    """Reset-In is seconds; Reset is the conventional absolute Unix timestamp."""
    try:
        if int(headers.get("x-ratelimit-remaining", "1")) > 0:
            return None
        relative = headers.get("x-ratelimit-reset-in")
        absolute = headers.get("x-ratelimit-reset")
        if relative is not None:
            seconds = float(relative)
        elif absolute is not None:
            seconds = float(absolute) - (time.time() if now is None else now)
        else:
            seconds = 1.0
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        return None
