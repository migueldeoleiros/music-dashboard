"""Minimal injectable HTTP transport (stdlib urllib) and shared rate limiter."""

import http.client
import math
import multiprocessing
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from typing import Any
from urllib.parse import urlsplit

from music_dashboard.domain.metadata.errors import MetadataTransientError


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = 30.0
    max_bytes: int | None = None  # honoured by bounded_transport only

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


def _fetch(request: HttpRequest) -> HttpResponse:
    """In-process http.client fetch with per-phase deadline checks and a size cap. NOT a hard
    total bound by itself (header reads and DNS can overrun); bounded_transport isolates it."""
    deadline = time.monotonic() + request.timeout
    max_bytes = request.max_bytes

    def left() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    parts = urlsplit(request.url)
    conn: http.client.HTTPConnection | None = None
    try:
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.netloc, timeout=left())
        elif parts.scheme == "http":
            conn = http.client.HTTPConnection(parts.netloc, timeout=left())
        else:
            raise MetadataTransientError("network error")
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        headers = {**request.headers, "Connection": "close"}
        conn.connect()
        conn.sock.settimeout(left())
        conn.request(request.method, target, request.body, headers)
        conn.sock.settimeout(left())
        resp = conn.getresponse()
        length = resp.getheader("content-length")
        if max_bytes is not None and length is not None and length.isdigit():
            if int(length) > max_bytes:
                raise MetadataTransientError("response too large")
        chunks: list[bytes] = []
        size = 0
        while True:
            conn.sock.settimeout(left()) if conn.sock else left()
            chunk = resp.read1(8192)  # read() would block until 8192 bytes
            if not chunk:
                break
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise MetadataTransientError("response too large")
            chunks.append(chunk)
        return HttpResponse(
            resp.status, {k.lower(): v for k, v in resp.getheaders()}, b"".join(chunks)
        )
    except MetadataTransientError:
        raise
    except (TimeoutError, OSError, HTTPException, ValueError):
        raise MetadataTransientError("network error") from None
    finally:
        if conn is not None:
            conn.close()


def _child_main(conn: Any, request: HttpRequest) -> None:
    """Spawned child: do the fetch, send ("ok", status, headers, body) or ("err",) and exit."""
    try:
        resp = _fetch(request)
        conn.send(("ok", resp.status, dict(resp.headers), resp.body))
    except BaseException:
        try:
            conn.send(("err",))
        except Exception:
            pass
    finally:
        conn.close()


def bounded_transport(request: HttpRequest) -> HttpResponse:
    """Process-isolated request; the caller's wait is bounded by `request.timeout`.

    The fetch runs in a freshly SPAWNED child (no inherited DB sockets/threads). The parent waits
    on a pipe for the remaining budget, so slow-drip headers/body, stuck DNS/connect and stalled
    servers cannot hold the caller past the budget; on expiry the child is killed. Body capped at
    `request.max_bytes` in the child. Process start-up counts against the budget.

    Documented residuals (not hard guarantees): once the pipe is readable, `recv()` is a blocking
    local-IPC read of a size-capped message and is not itself time-bounded; cleanup after the
    wait is a kill plus `join(1.0)`, so a child that cannot be reaped in 1s (e.g. uninterruptible
    state) may linger briefly as a daemon process. Failures raise MetadataTransientError without
    URL details."""
    deadline = time.monotonic() + request.timeout
    ctx = multiprocessing.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(send, request), daemon=True)
    try:
        proc.start()
        send.close()
        if not recv.poll(max(0.0, deadline - time.monotonic())):
            raise MetadataTransientError("network error")
        msg = recv.recv()
    except MetadataTransientError:
        raise
    except Exception:  # EOFError (child died), OSError, unpickling
        raise MetadataTransientError("network error") from None
    finally:
        recv.close()
        send.close()
        if proc.is_alive():
            proc.kill()
        if proc.pid is not None:
            proc.join(1.0)
    if msg[0] != "ok":
        raise MetadataTransientError("network error")
    return HttpResponse(msg[1], msg[2], msg[3])


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

    def pending_wait(self) -> float:
        """Seconds the next acquire() would sleep (for deadline-bounded callers)."""
        with self._lock:
            return max(0.0, self._next_allowed - self._clock())

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
