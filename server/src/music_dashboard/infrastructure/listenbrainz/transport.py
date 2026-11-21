"""Shared synchronous ListenBrainz HTTP transport (stdlib ``urllib`` only).

Public API (used by ``listens.py`` and the enrichment adapter ``metadata.py``)::

    http = UrllibHttpTransport()                       # or any HttpTransport (mocks)
    transport = ListenBrainzTransport(http, token=settings_token)   # token: worker only
    data: dict = transport.request("POST", "/1/metadata/recording/", {"recording_mbids": [...]})
    data: dict = transport.request("POST", "/1/export/", authenticated=True)
    blob: bytes = transport.download(url_or_path, authenticated=True)

``request(method, path, body=None, *, authenticated=False) -> dict`` sends JSON, returns the
decoded JSON object (``{}`` for an empty 204 body). ``path`` must be an absolute path on the
configured LB origin (``/1/...``, optional query string); anything else is rejected.
``authenticated=True`` adds ``Authorization: Token ...`` and requires a token.

Safety rules:
* HTTPS only; redirects are never followed (a 3xx is an error), so credentials cannot be
  forwarded elsewhere.
* The token is only ever attached to requests for the LB origin. ``download`` accepts absolute
  URLs only for the LB host or explicitly allow-listed hosts, and never sends the token to the
  latter.
* Errors never contain the token, request URL, query string, headers, or response bodies;
  network failures are reduced to the exception class name.
* 429 / 5xx / network errors are retried with bounded exponential backoff (injectable
  ``sleep``/``clock``); ``Retry-After`` (seconds or HTTP-date) and
  ``X-RateLimit-Reset-In`` are honoured exactly:
  if the server asks for longer than ``max_backoff`` the transport raises ``RateLimitedError``
  (with ``retry_after``) instead of retrying early; the caller owns durable backoff. When
  ``X-RateLimit-Remaining`` reaches 0 the next request waits for the reset window (or raises
  ``RateLimitedError`` if it exceeds ``max_backoff``). Default response body cap: 64 MiB.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.listenbrainz.org"
DEFAULT_USER_AGENT = (
    "music-dashboard/0.1 (self-hosted personal ListenBrainz dashboard; "
    "https://github.com/music-dashboard)"
)
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_CONTROL = re.compile(r"[\x00-\x20\x7f\\]")


class ListenBrainzError(Exception):
    """Base error. Messages are sanitized: no token, URL or body content."""


class ConfigurationError(ListenBrainzError):
    """Missing token or invalid transport configuration."""


class TransportError(ListenBrainzError):
    """Network-level failure (sanitized to the exception class name)."""


class HttpStatusError(ListenBrainzError):
    def __init__(self, status: int) -> None:
        super().__init__(f"ListenBrainz returned HTTP {status}")
        self.status = status


class RateLimitedError(HttpStatusError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__(429)
        self.retry_after = retry_after


class ProtocolError(ListenBrainzError):
    """Response does not have the expected shape."""


@dataclass(frozen=True, repr=False)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout: float

    def __repr__(self) -> str:  # never expose URL or headers (Authorization)
        return f"HttpRequest({self.method})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    """Synchronous HTTP port. Must NOT follow redirects; must raise ``OSError`` on network
    failure and return 4xx/5xx/3xx as ordinary responses."""

    def send(self, request: HttpRequest) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class UrllibHttpTransport:
    def __init__(self, *, max_body_bytes: int = 64 << 20) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._max = max_body_bytes

    def send(self, request: HttpRequest) -> HttpResponse:
        req = urllib.request.Request(  # noqa: S310 - scheme validated by caller
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            with self._opener.open(req, timeout=request.timeout) as resp:
                return HttpResponse(resp.status, dict(resp.headers), self._read(resp))
        except urllib.error.HTTPError as exc:
            try:
                return HttpResponse(exc.code, dict(exc.headers), b"")
            finally:
                exc.close()

    def _read(self, resp: Any) -> bytes:
        data: bytes = resp.read(self._max + 1)
        if len(data) > self._max:
            raise OSError("response too large")
        return data


@dataclass(repr=False)
class ListenBrainzTransport:
    http: HttpTransport
    token: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    allowed_download_hosts: frozenset[str] = frozenset()
    timeout: float = 30.0
    max_retries: int = 4
    base_backoff: float = 1.0
    max_backoff: float = 60.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], float] = time.time
    _not_before: float = field(default=0.0, init=False, repr=False)
    _origin: tuple[str, int] = field(default=("", 0), init=False, repr=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.path.strip("/"):
            raise ConfigurationError("base_url must be a bare https origin")
        self._origin = (parts.hostname.lower(), parts.port or 443)
        self.base_url = f"https://{parts.netloc}"
        if self.max_retries < 0 or self.max_backoff < 0 or self.base_backoff < 0:
            raise ConfigurationError("retry settings must be non-negative")

    def __repr__(self) -> str:
        return f"ListenBrainzTransport(origin={self._origin[0]})"

    # ---- public API -------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        url = self._url_for_path(path)
        payload = None if body is None else json.dumps(body).encode()
        resp = self._send(method.upper(), url, payload, authenticated=authenticated)
        if not resp.body.strip():
            return {}
        try:
            data = json.loads(resp.body)
        except ValueError:
            raise ProtocolError("response is not valid JSON") from None
        if not isinstance(data, dict):
            raise ProtocolError("response JSON is not an object")
        return data

    def download(self, url_or_path: str, *, authenticated: bool = False) -> bytes:
        """GET raw bytes from an LB path or a validated absolute https URL."""
        if url_or_path.startswith("/"):
            url, on_origin = self._url_for_path(url_or_path), True
        else:
            url, on_origin = self._validate_absolute_url(url_or_path)
        return self._send(
            "GET", url, None, authenticated=authenticated and on_origin, accept="*/*"
        ).body

    # ---- internals --------------------------------------------------------------------
    def _url_for_path(self, path: str) -> str:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or _CONTROL.search(path)
            or ".." in path.split("?", 1)[0].split("/")
        ):
            raise ConfigurationError("invalid request path")
        return self.base_url + path

    def _validate_absolute_url(self, url: str) -> tuple[str, bool]:
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:
            raise ConfigurationError("invalid download URL") from None
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or _CONTROL.search(url)
            or port not in (None, 443)
        ):
            raise ConfigurationError("invalid download URL")
        on_origin = (host, port or 443) == self._origin
        if not on_origin and host not in {h.lower() for h in self.allowed_download_hosts}:
            raise ConfigurationError("download host is not allowed")
        return url, on_origin

    def _headers(self, authenticated: bool, has_body: bool, accept: str) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        if has_body:
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self.token:
                raise ConfigurationError("ListenBrainz token is required for this request")
            headers["Authorization"] = f"Token {self.token}"
        return headers

    def _wait_for_window(self) -> None:
        delay = self._not_before - self.clock()
        if delay > self.max_backoff:
            raise RateLimitedError(delay)  # never retry early; caller owns durable backoff
        if delay > 0:
            self.sleep(delay)

    def _send(
        self,
        method: str,
        url: str,
        body: bytes | None,
        *,
        authenticated: bool,
        accept: str = "application/json",
    ) -> HttpResponse:
        req = HttpRequest(
            method, url, self._headers(authenticated, body is not None, accept), body, self.timeout
        )
        attempt = 0
        while True:
            self._wait_for_window()
            wait: float | None
            try:
                resp = self.http.send(req)
            except OSError as exc:  # URLError/timeouts are OSError subclasses
                if attempt >= self.max_retries:
                    raise TransportError(f"network error ({type(exc).__name__})") from None
                wait = None
            else:
                self._note_rate_limit(resp.headers)
                if 200 <= resp.status < 300:
                    return resp
                retry_after = self._retry_after(resp.headers)
                if resp.status == 429:
                    if attempt >= self.max_retries or (
                        retry_after is not None and retry_after > self.max_backoff
                    ):
                        raise RateLimitedError(retry_after)
                    wait = retry_after
                elif resp.status in _RETRY_STATUSES:
                    if attempt >= self.max_retries:
                        raise HttpStatusError(resp.status)
                    wait = None
                else:
                    raise HttpStatusError(resp.status)
            backoff = min(self.max_backoff, self.base_backoff * 2**attempt)
            self.sleep(wait if wait is not None else backoff)
            attempt += 1

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        for key, value in headers.items():
            if key.lower() == name:
                return value
        return None

    def _number(self, headers: Mapping[str, str], name: str) -> float | None:
        raw = self._header(headers, name)
        try:
            value = float(raw) if raw is not None else None
        except ValueError:
            return None
        return value if value is not None and value >= 0 and value == value else None

    def _http_date(self, raw: str) -> float | None:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, when.timestamp() - self.wall_clock())

    def _retry_after(self, headers: Mapping[str, str]) -> float | None:
        value = self._number(headers, "retry-after")
        if value is None:
            raw = self._header(headers, "retry-after")
            value = self._http_date(raw) if raw else None
        if value is None:
            value = self._number(headers, "x-ratelimit-reset-in")
        return value

    def _note_rate_limit(self, headers: Mapping[str, str]) -> None:
        remaining = self._number(headers, "x-ratelimit-remaining")
        reset = self._number(headers, "x-ratelimit-reset-in")
        if remaining is not None and remaining < 1 and reset is not None:
            self._not_before = self.clock() + reset
