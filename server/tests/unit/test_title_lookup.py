import json

import pytest

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
    TitleDeadlineError,
)
from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
from music_dashboard.infrastructure.musicbrainz.title_lookup import MusicBrainzTitleClient
from music_dashboard.infrastructure.musicbrainz.transport import HttpResponse, RateLimiter

ID = "11111111-1111-4111-8111-111111111111"
RG = "22222222-2222-4222-8222-222222222222"
CFG = MusicBrainzConfig(user_agent="t/1 (mailto:a@b.org)")


def client(status=200, body=None, headers=None, limiter=None, seen=None):
    def transport(req):
        if seen is not None:
            seen.append(req)
        return HttpResponse(status, headers or {}, json.dumps(body).encode())

    return MusicBrainzTitleClient(CFG, transport, limiter or RateLimiter(0, sleep=lambda s: None))


def test_release_verified_with_release_group():
    seen = []
    c = client(body={"id": ID, "title": "T", "release-group": {"id": RG}}, seen=seen)
    assert c.title("release", ID.upper()) == {"title": "T", "release_group_mbid": RG}
    assert "inc=release-groups" in seen[0].url


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("release", {"id": RG, "title": "T", "release-group": {"id": RG}}),  # id mismatch
        ("release", {"id": ID, "title": "T", "primary-type": "Album"}),  # RG shape for release
        ("release_group", {"id": ID, "title": "T", "release-group": {"id": RG}}),  # release shape
        ("release_group", {"id": ID, "title": "T"}),  # no type evidence
        ("release_group", {"id": ID, "title": "  ", "primary-type": "Album"}),
        ("release_group", {"id": ID, "title": 3, "primary-type": "Album"}),
    ],
)
def test_rejects_wrong_identity_or_type(kind, body):
    with pytest.raises(MetadataPermanentError):
        client(body=body).title(kind, ID)


def test_release_group_ok_404_and_transient():
    assert client(
        body={"id": ID, "title": "G", "primary-type": None, "first-release-date": ""}
    ).title("release_group", ID) == {"title": "G", "release_group_mbid": None}
    assert client(404, {}).title("release", ID) is None
    with pytest.raises(MetadataTransientError) as e:
        client(503, {}, {"retry-after": "42"}).title("release", ID)
    assert e.value.retry_after == 42


def test_deadline_covers_limiter_wait_and_request_timeout():
    now = [0.0]
    lim = RateLimiter(10, clock=lambda: now[0], sleep=lambda s: None)
    lim.acquire()
    seen = []
    c = client(body={"id": ID, "title": "G", "primary-type": "Album"}, limiter=lim, seen=seen)
    with pytest.raises(TitleDeadlineError):
        c.title("release_group", ID, timeout=5)
    assert not seen
    now[0] = 8
    c.title("release_group", ID, timeout=5)
    assert seen[0].timeout == pytest.approx(3)


def test_policy_rejects_non_finite_and_decline_short_budget():
    import math
    from datetime import timedelta

    from music_dashboard.application.resolve_titles import TitlePolicy

    for kw in (
        {"deadline_seconds": math.nan},
        {"deadline_seconds": math.inf},
        {"retry_base_seconds": math.inf},
        {"retry_max_seconds": math.nan},
        {"max_requests": 0},
        {"not_found_ttl": timedelta(0)},
    ):
        with pytest.raises(ValueError):
            TitlePolicy(**kw)
    seen = []
    c = client(body={"id": ID, "title": "G", "primary-type": "A"}, seen=seen)
    with pytest.raises(TitleDeadlineError):
        c.title("release_group", ID, timeout=0.05)
    assert not seen


def _serve(handler_fn):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                handler_fn(self)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, timeout, max_bytes=1000):
    from music_dashboard.infrastructure.musicbrainz.transport import HttpRequest

    return HttpRequest(
        "GET", f"http://127.0.0.1:{srv.server_port}/x?fmt=json", {}, None, timeout, max_bytes
    )


def test_bounded_transport_total_deadline_slow_drip_and_size_cap():
    import time

    from music_dashboard.infrastructure.musicbrainz.transport import bounded_transport

    def drip(h):
        h.send_response(200)
        h.send_header("Content-Length", "100")
        h.end_headers()
        for _ in range(100):
            h.wfile.write(b"x")
            h.wfile.flush()
            time.sleep(0.1)  # each recv is well inside a per-socket timeout

    def big(h):
        h.send_response(200)
        h.end_headers()
        h.wfile.write(b"y" * 5000)

    def declared_big(h):
        h.send_response(200)
        h.send_header("Content-Length", "99999")
        h.end_headers()

    def ok(h):
        h.send_response(200)
        h.send_header("Content-Length", "2")
        h.end_headers()
        h.wfile.write(b"{}")

    def header_drip(h):
        # status line + headers dripped forever: getresponse() never completes
        h.wfile.write(b"HTTP/1.1 200 OK\r\n")
        for _ in range(200):
            h.wfile.write(b"X-A: b\r\n")
            h.wfile.flush()
            time.sleep(0.1)

    def stall(h):
        time.sleep(3)  # accepts, never answers

    for fn, timeout in ((drip, 2), (header_drip, 2), (stall, 2), (big, 5), (declared_big, 5)):
        srv = _serve(fn)
        start = time.monotonic()
        with pytest.raises(MetadataTransientError):
            bounded_transport(_req(srv, timeout))
        assert time.monotonic() - start < timeout + 1.5
        srv.shutdown()
    import multiprocessing

    assert multiprocessing.active_children() == []  # every child reaped
    srv = _serve(ok)
    resp = bounded_transport(_req(srv, 5))
    assert (resp.status, resp.body) == (200, b"{}")
    srv.shutdown()


def test_title_client_clamps_delays_but_shared_parsers_do_not():
    from music_dashboard.infrastructure.musicbrainz.title_lookup import MAX_DELAY_SECONDS
    from music_dashboard.infrastructure.musicbrainz.transport import (
        parse_retry_after,
        rate_limit_exhausted_delay,
    )

    # shared semantics unchanged
    assert parse_retry_after({"retry-after": "1e300"}) == 1e300
    assert parse_retry_after({"retry-after": "1e999"}) is None
    lim = RateLimiter(1, clock=lambda: 0.0, sleep=lambda s: None)
    lim.defer(1e300)
    assert lim.pending_wait() == 1e300
    # title client clamps for limiter and error
    for headers in (
        {"retry-after": "1e300"},
        {"retry-after": "Fri, 31 Dec 9999 23:59:59 GMT"},
        {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1e300"},
        {"x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "1e30"},
    ):
        lim = RateLimiter(0, clock=lambda: 0.0, sleep=lambda s: None)
        with pytest.raises(MetadataTransientError) as e:
            client(503, {}, headers, limiter=lim).title("release", ID)
        assert e.value.retry_after == MAX_DELAY_SECONDS
        assert lim.pending_wait() == MAX_DELAY_SECONDS
    assert (
        rate_limit_exhausted_delay({"x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "1e30"})
        == 1e30
    )


def test_error_retry_after_is_max_of_all_header_delays():
    lim = RateLimiter(0, clock=lambda: 0.0, sleep=lambda s: None)
    headers = {"retry-after": "30", "x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "300"}
    with pytest.raises(MetadataTransientError) as e:
        client(429, {}, headers, limiter=lim).title("release", ID)
    assert e.value.retry_after == 300 == lim.pending_wait()
