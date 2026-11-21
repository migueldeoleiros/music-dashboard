"""Unauthenticated HTTP transport for artwork sources (MusicBrainz, Deezer, CAA, Wikidata).

Deliberately separate from the ListenBrainz transport: it can never carry credentials. Requests
with credential-like headers are refused, the host must be on the source allowlist, and
redirects are followed only to https hosts on that same allowlist (CAA -> archive.org,
Wikidata entity redirects).
"""

import urllib.error
import urllib.request
from http.client import HTTPException
from typing import Any
from urllib.parse import urlsplit

from music_dashboard.domain.artwork.errors import ArtworkTransientError
from music_dashboard.infrastructure.artwork.urls import host_allowed
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    HttpResponse,
    HttpTransport,
)

SOURCE_HOSTS = frozenset({"musicbrainz.org", "api.deezer.com", "www.wikidata.org"})
FORBIDDEN_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key"})
__all__ = ["HttpRequest", "HttpResponse", "HttpTransport", "unauthenticated_transport"]


def source_host_allowed(host: str) -> bool:
    return host in SOURCE_HOSTS or host_allowed(host)


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not source_host_allowed(parts.hostname or "")
    ):
        raise ValueError("artwork request URL is not an allowed https source")


def check_request(request: HttpRequest) -> None:
    if any(name.lower() in FORBIDDEN_HEADERS for name in request.headers):
        raise ValueError("artwork transport never sends credentials")
    _check_url(request.url)


class _AllowlistedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ) -> urllib.request.Request | None:
        try:
            _check_url(newurl)
        except ValueError:
            return None  # surfaces the 3xx as an ordinary (non-success) response
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_AllowlistedRedirects)


def unauthenticated_transport(request: HttpRequest) -> HttpResponse:
    check_request(request)
    req = urllib.request.Request(  # noqa: S310 - https + allowlisted host enforced above
        request.url, data=None, headers=dict(request.headers), method=request.method
    )
    try:
        with _opener.open(req, timeout=request.timeout) as resp:
            body: Any = b"" if request.method == "HEAD" else resp.read(2_000_000)
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()}, b"")
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
        raise ArtworkTransientError("network error") from None
