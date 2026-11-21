import io
import json
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from typing import Any

import pytest

from music_dashboard.infrastructure.listenbrainz.listens import (
    ExportAccountMismatchError,
    ExportCorruptError,
    ExportError,
    ExportFailedError,
    ExportIncompleteError,
    ExportTimeoutError,
    ExportTooLargeError,
    ListenBrainzClient,
)
from music_dashboard.infrastructure.listenbrainz.transport import (
    ConfigurationError,
    HttpRequest,
    HttpResponse,
    HttpStatusError,
    ListenBrainzTransport,
    ProtocolError,
    RateLimitedError,
    ResponseTooLargeError,
    TransportError,
    UrllibHttpTransport,
)

TOKEN = "s3cr3t-token-value"
Handler = Callable[[HttpRequest], HttpResponse | Exception]


class FakeHttp:
    def __init__(self, *responses: HttpResponse | Exception | Handler) -> None:
        self.queue = list(responses)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if callable(item):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        return item


def ok(data: Any, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(200, headers or {}, json.dumps(data).encode())


def status(code: int, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(code, headers or {}, b"secret body " + TOKEN.encode())


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make(
    http: FakeHttp, clock: Clock | None = None, token: str | None = TOKEN, **kw: Any
) -> ListenBrainzTransport:
    clock = clock or Clock()
    return ListenBrainzTransport(http, token=token, sleep=clock.sleep, clock=clock, **kw)


def listen(ts: int, msid: str = "m1") -> dict[str, Any]:
    return {
        "listened_at": ts,
        "recording_msid": str(uuid.uuid5(uuid.NAMESPACE_DNS, msid)),
        "track_metadata": {"track_name": "T", "additional_info": {"x": [1, {"y": None}]}},
        "mbid_mapping": {"recording_mbid": "r"},
    }


def make_zip(
    records: list[Any] | None = None,
    name: str = "listens/2026/01.jsonl",
    raw: bytes | None = None,
    username: str | None = "u",
) -> bytes:
    """Real archive layout: user.json, listens/YYYY/MM.jsonl and an ignored feedback.jsonl."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        data = raw if raw is not None else "".join(json.dumps(r) + "\n" for r in records or [])
        zf.writestr(name, data)
        if username is not None:
            zf.writestr("user.json", json.dumps({"user_id": 7, "username": username}))
        zf.writestr("feedback.jsonl", '{"recording_msid": "ignored", "score": 1}\n')
    return buf.getvalue()


def zip_ok(blob: bytes) -> HttpResponse:
    return HttpResponse(200, {"Content-Type": "application/zip"}, blob)


# ---------------------------------------------------------------- transport


def test_headers_https_user_agent_and_auth_only_when_requested() -> None:
    http = FakeHttp(ok({}))
    t = make(http)
    t.request("GET", "/1/user/x/listen-count")
    t.request("POST", "/1/export/", authenticated=True)
    public, authed = http.requests
    assert public.url.startswith("https://api.listenbrainz.org/1/")
    assert "Authorization" not in public.headers
    assert authed.headers["Authorization"] == f"Token {TOKEN}"
    assert "music-dashboard" in public.headers["User-Agent"]
    assert TOKEN not in repr(t) and TOKEN not in repr(authed)


def test_authenticated_without_token_is_actionable_config_error() -> None:
    with pytest.raises(ConfigurationError, match="token"):
        make(FakeHttp(ok({})), token=None).request("POST", "/1/export/", authenticated=True)


@pytest.mark.parametrize("base", ["http://api.listenbrainz.org", "https://u:p@x.org", ""])
def test_base_url_must_be_https_origin(base: str) -> None:
    with pytest.raises(ConfigurationError):
        make(FakeHttp(ok({})), base_url=base)


@pytest.mark.parametrize(
    "path", ["https://evil.test/1/x", "//evil.test/1", "1/x", "/1/../x", "/1/a b", "/1/a\\b"]
)
def test_request_path_validation(path: str) -> None:
    with pytest.raises(ConfigurationError):
        make(FakeHttp(ok({}))).request("GET", path)


def test_non_object_or_invalid_json_rejected_empty_body_ok() -> None:
    assert make(FakeHttp(HttpResponse(204, {}, b""))).request("GET", "/1/x") == {}
    with pytest.raises(ProtocolError):
        make(FakeHttp(ok([1]))).request("GET", "/1/x")
    with pytest.raises(ProtocolError):
        make(FakeHttp(HttpResponse(200, {}, b"<html"))).request("GET", "/1/x")


def test_429_retry_after_backoff_then_success() -> None:
    clock = Clock()
    http = FakeHttp(status(429, {"Retry-After": "7"}), status(429, {"retry-after": "30"}), ok({}))
    make(http, clock, max_backoff=30).request("GET", "/1/x")
    assert clock.sleeps == [7, 30]
    assert len(http.requests) == 3


@pytest.mark.parametrize("header", ["Retry-After", "X-RateLimit-Reset-In"])
def test_429_longer_than_max_backoff_raises_without_early_retry(header: str) -> None:
    clock = Clock()
    http = FakeHttp(status(429, {header: "9999"}), ok({}))
    with pytest.raises(RateLimitedError) as err:
        make(http, clock, max_backoff=30).request("GET", "/1/x")
    assert err.value.retry_after == 9999 and clock.sleeps == [] and len(http.requests) == 1


@pytest.mark.parametrize("header", ["Retry-After", "X-RateLimit-Reset-In"])
@pytest.mark.parametrize(
    "bad", ["inf", "-inf", "nan", "Infinity", "1e999", "-5", "1_0", "0x10", ""]
)
def test_non_finite_or_malformed_delay_headers_are_ignored(header: str, bad: str) -> None:
    clock = Clock()
    http = FakeHttp(status(429, {header: bad}), ok({}))
    make(http, clock).request("GET", "/1/x")
    assert clock.sleeps == [1.0]  # fell back to ordinary backoff, never inf/nan


@pytest.mark.parametrize("header", ["Retry-After", "X-RateLimit-Reset-In"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("86399", 86399), ("86400", 86400), ("86401", 86400), ("99999999999999999999", 86400)],
)
def test_delay_header_clamped_to_24h_boundary(header: str, raw: str, expected: float) -> None:
    http = FakeHttp(status(429, {header: raw}), ok({}))
    with pytest.raises(RateLimitedError) as err:
        make(http, max_backoff=30).request("GET", "/1/x")
    assert err.value.retry_after == expected and len(http.requests) == 1


def test_far_future_http_date_clamped_and_year_overflow_ignored() -> None:
    http = FakeHttp(status(429, {"Retry-After": "Fri, 31 Dec 9999 23:59:59 GMT"}), ok({}))
    with pytest.raises(RateLimitedError) as err:
        make(http, max_backoff=30).request("GET", "/1/x")
    assert err.value.retry_after == 86400
    http = FakeHttp(status(429, {"Retry-After": "Fri, 31 Dec 99999999 23:59:59 GMT"}), ok({}))
    clock = Clock()
    make(http, clock).request("GET", "/1/x")
    assert clock.sleeps == [1.0]


def test_exhausted_window_with_oversized_reset_is_clamped() -> None:
    http = FakeHttp(ok({}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "1e999"}))
    t = make(http, max_backoff=30)
    t.request("GET", "/1/x")  # inf reset ignored: next request is not blocked
    t.request("GET", "/1/x")
    assert len(http.requests) == 2
    http = FakeHttp(ok({}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "9" * 15}))
    t = make(http, max_backoff=30)
    t.request("GET", "/1/x")
    with pytest.raises(RateLimitedError) as err:
        t.request("GET", "/1/x")
    assert err.value.retry_after <= 86400


class _Resp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status, self.headers, self._b = 200, headers or {}, body

    def read(self, n: int) -> bytes:
        return self._b[:n]

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "100"}])
def test_oversized_response_fails_distinctly_without_retry(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    http = UrllibHttpTransport(max_body_bytes=10)
    calls: list[int] = []

    def opener(*a: Any, **k: Any) -> _Resp:
        calls.append(1)
        return _Resp(b"x" * 100, headers)

    monkeypatch.setattr(http._opener, "open", opener)
    clock = Clock()
    with pytest.raises(ResponseTooLargeError) as err:
        make(http, clock, max_retries=4).download("/1/export/1/download", authenticated=True)  # type: ignore[arg-type]
    assert len(calls) == 1 and clock.sleeps == []
    assert TOKEN not in str(err.value)


def test_response_at_exact_cap_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    http = UrllibHttpTransport(max_body_bytes=10)
    monkeypatch.setattr(http._opener, "open", lambda *a, **k: _Resp(b"x" * 10))
    assert http.send(HttpRequest("GET", "https://x/1", {}, None, 1)).body == b"x" * 10


def test_export_download_too_large_is_distinct_export_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seq = export_http(b"")
    n = {"i": 0}

    class Http:
        def send(self, request: HttpRequest) -> HttpResponse:
            n["i"] += 1
            if request.url.endswith("/download"):
                raise ResponseTooLargeError("x")
            return seq.send(request)

    with pytest.raises(ExportTooLargeError) as err:
        client(Http()).export("u")  # type: ignore[arg-type]
    assert not isinstance(err.value, ExportCorruptError)


def test_429_retry_after_http_date() -> None:
    clock = Clock()
    http = FakeHttp(status(429, {"Retry-After": "Wed, 21 Oct 2015 07:28:10 GMT"}), ok({}))
    t = make(http, clock, wall_clock=lambda: 1445412480.0 - 5)  # 07:28:00 GMT + ... -5s
    t.request("GET", "/1/x")
    assert clock.sleeps == [15]


def test_exhausted_rate_window_beyond_max_backoff_raises_before_sending() -> None:
    http = FakeHttp(ok({}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "500"}))
    t = make(http, max_backoff=30)
    t.request("GET", "/1/x")
    with pytest.raises(RateLimitedError):
        t.request("GET", "/1/x")
    assert len(http.requests) == 1


def test_429_exhaustion_is_bounded_and_sanitized() -> None:
    clock = Clock()
    http = FakeHttp(status(429, {"X-RateLimit-Reset-In": "3"}))
    with pytest.raises(RateLimitedError) as err:
        make(http, clock, max_retries=2).request("GET", "/1/x?token=abc")
    assert err.value.retry_after == 3
    assert len(http.requests) == 3 and clock.sleeps == [3, 3]
    assert TOKEN not in str(err.value) and "abc" not in str(err.value)


def test_rate_limit_headers_delay_next_request() -> None:
    clock = Clock()
    http = FakeHttp(
        ok({}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "4"}),
        ok({}, {"X-RateLimit-Remaining": "9", "X-RateLimit-Reset-In": "4"}),
    )
    t = make(http, clock)
    t.request("GET", "/1/x")
    assert clock.sleeps == []
    t.request("GET", "/1/x")
    assert clock.sleeps == [4]
    t.request("GET", "/1/x")
    assert clock.sleeps == [4]


def test_5xx_retries_exponentially_then_raises_without_body_leak() -> None:
    clock = Clock()
    http = FakeHttp(status(503))
    with pytest.raises(HttpStatusError) as err:
        make(http, clock, max_retries=3, base_backoff=1, max_backoff=3).request("GET", "/1/x")
    assert clock.sleeps == [1, 2, 3]
    assert err.value.status == 503 and TOKEN not in str(err.value)


def test_4xx_not_retried() -> None:
    http = FakeHttp(status(401))
    with pytest.raises(HttpStatusError) as err:
        make(http).request("GET", "/1/x")
    assert err.value.status == 401 and len(http.requests) == 1


def test_network_error_sanitized() -> None:
    leak = urllib.error.URLError(f"https://api.listenbrainz.org/1/x?token={TOKEN} refused")
    clock = Clock()
    with pytest.raises(TransportError) as err:
        make(FakeHttp(leak), clock, max_retries=1).request("GET", "/1/x")
    text = f"{err.value!s}{err.value!r}"
    assert "URLError" in text and TOKEN not in text and "listenbrainz.org" not in text
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert len(clock.sleeps) == 1


def test_urllib_transport_sanitizes_and_returns_3xx(monkeypatch: pytest.MonkeyPatch) -> None:
    http = UrllibHttpTransport()

    def boom(*a: Any, **k: Any) -> Any:
        raise urllib.error.HTTPError("https://x", 302, "Found", {"Location": "https://e"}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(http._opener, "open", boom)
    resp = http.send(HttpRequest("GET", "https://x/1", {}, None, 1))
    assert resp.status == 302
    t = make(FakeHttp(resp))
    with pytest.raises(HttpStatusError):  # redirects never followed
        t.request("GET", "/1/x")


def test_download_host_validation_and_credential_scoping() -> None:
    http = FakeHttp(HttpResponse(200, {}, b"PK"))
    t = make(http, allowed_download_hosts=frozenset({"cdn.example.org"}))
    for bad in (
        "http://api.listenbrainz.org/x",
        "https://evil.test/x",
        "https://api.listenbrainz.org@evil.test/x",
        "https://user:p@api.listenbrainz.org/x",
        "https://api.listenbrainz.org:8443/x",
        "https://cdn.example.org.evil.test/x",
    ):
        with pytest.raises(ConfigurationError):
            t.download(bad, authenticated=True)
    assert http.requests == []
    t.download("https://cdn.example.org/f.zip", authenticated=True)
    t.download("https://api.listenbrainz.org/1/export/download/1", authenticated=True)
    foreign, own = http.requests
    assert "Authorization" not in foreign.headers
    assert own.headers["Authorization"] == f"Token {TOKEN}"


# ---------------------------------------------------------------- listens


def client(http: FakeHttp, clock: Clock | None = None, **kw: Any) -> ListenBrainzClient:
    clock = clock or Clock()
    return ListenBrainzClient(make(http, clock), **kw)


def test_page_query_and_payload_preserved() -> None:
    rows = [listen(5), listen(5, "m2")]
    http = FakeHttp(ok({"payload": {"listens": rows}}))
    got = client(http).page("some user/x", max_ts=10, count=2)
    assert got == rows
    url = http.requests[0].url
    assert "/1/user/some%20user%2Fx/listens?" in url and "count=2" in url and "max_ts=10" in url
    assert "Authorization" not in http.requests[0].headers
    client(http).page("u", min_ts=3)
    assert "min_ts=3" in http.requests[-1].url and "max_ts" not in http.requests[-1].url


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": 0},
        {"count": 1001},
        {"count": True},
        {"count": 1.5},
        {"max_ts": 1, "min_ts": 2},
        {"max_ts": -1},
        {"min_ts": -5},
        {"max_ts": "1"},
        {"max_ts": True},
    ],
)
def test_page_invalid_bounds(kwargs: dict[str, Any]) -> None:
    http = FakeHttp(ok({"payload": {"listens": []}}))
    with pytest.raises(ValueError):
        client(http).page("u", **kwargs)
    assert http.requests == []


def test_page_and_count_malformed_payloads() -> None:
    for bad in ({}, {"payload": {"listens": "x"}}, {"payload": {"listens": [1]}}):
        with pytest.raises(ProtocolError):
            client(FakeHttp(ok(bad))).page("u")
    for bad in ({"payload": {}}, {"payload": {"count": "3"}}, {"payload": {"count": -1}}):
        with pytest.raises(ProtocolError):
            client(FakeHttp(ok(bad))).listen_count("u")
    assert client(FakeHttp(ok({"payload": {"count": 42}}))).listen_count("u") == 42
    with pytest.raises(ValueError):
        client(FakeHttp(ok({}))).page(" ")


# ---------------------------------------------------------------- export

RECORDS = [listen(1), listen(1, "m2")]


def export_http(
    blob: bytes,
    states: list[dict[str, Any]] | None = None,
    count: int = 2,
    create: dict[str, Any] | None = None,
    with_count: bool = True,
    owner: dict[str, Any] | None = None,
) -> FakeHttp:
    states = states if states is not None else [{"status": "completed", "listen_count": 2}]
    seq: list[HttpResponse] = [
        ok(owner if owner is not None else {"valid": True, "user_name": "U"}),
        *([ok({"payload": {"count": count}})] if with_count else []),
        ok(create if create is not None else {"export_id": 77}),
        *[ok(s) for s in states],
        zip_ok(blob),
    ]
    return FakeHttp(*seq)


def rows_of(snap: Any) -> list[dict[str, Any]]:
    return list(snap.rows)


def test_export_happy_path_complete_snapshot_and_calls() -> None:
    http = export_http(
        make_zip(RECORDS),
        [
            {"status": "waiting"},
            {"status": "in_progress"},
            {"status": "complete", "listen_count": 2},
        ],
    )
    clock = Clock()
    snap = client(http, clock, poll_interval=2).export("u")
    assert snap.complete and snap.expected_count == 2 and snap.user == "u"
    assert not isinstance(snap.rows, list)  # lazy, nothing decoded yet
    assert rows_of(snap) == RECORDS  # metadata preserved verbatim
    calls = [(r.method, r.url.split(".org")[1]) for r in http.requests]
    assert calls == [
        ("GET", "/1/validate-token"),
        ("GET", "/1/user/u/listen-count"),
        ("POST", "/1/export/"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/77/download"),
    ]
    assert all("Authorization" in r.headers for r in http.requests if "listen-count" not in r.url)
    assert clock.sleeps == [2, 2]


def test_export_without_manifest_is_not_complete_but_usable() -> None:
    snap = client(export_http(make_zip(RECORDS), [{"status": "complete"}])).export("u")
    assert not snap.complete and snap.expected_count is None
    assert rows_of(snap) == RECORDS
    for bad in ("2", True, -1, None, 2.0):
        snap = client(
            export_http(make_zip(RECORDS), [{"status": "ready", "listen_count": bad}])
        ).export("u")
        assert not snap.complete and snap.expected_count is None


def test_export_alt_shapes_id_alias_status_case_and_nested_member() -> None:
    http = export_http(
        make_zip(RECORDS, name="listens/2020/12.jsonl"),
        [{"status": "Completed", "listen_count": 2}],
        create={"id": "abc-1"},
    )
    assert rows_of(client(http).export("u")) == RECORDS
    assert http.requests[3].url.endswith("/1/export/abc-1")
    assert http.requests[-1].url.endswith("/1/export/abc-1/download")


@pytest.mark.parametrize(
    "owner",
    [
        {"valid": True, "user_name": "someone-else"},
        {"valid": False, "user_name": "U"},
        {"valid": True},
        {"user_name": "U"},
        {"valid": "true", "user_name": "U"},
        {},
    ],
)
def test_export_token_owner_mismatch_or_unvalidated_aborts_before_export(
    owner: dict[str, Any],
) -> None:
    http = export_http(make_zip(RECORDS), owner=owner)
    with pytest.raises(ExportAccountMismatchError) as err:
        client(http).export("u")
    assert TOKEN not in str(err.value)
    assert [r.url.split(".org")[1] for r in http.requests] == ["/1/validate-token"]


def test_export_owner_match_is_casefold() -> None:
    http = export_http(
        make_zip(RECORDS, username="MIGUEL"), owner={"valid": True, "user_name": "MiGuEl"}
    )
    assert client(http).export("miguel").complete


def test_export_status_download_url_is_never_followed() -> None:
    http = export_http(
        make_zip(RECORDS), [{"status": "completed", "download_url": "https://evil.test/z"}]
    )
    assert rows_of(client(http).export("u"))
    assert all("evil" not in r.url for r in http.requests)
    assert http.requests[-1].url.endswith("/1/export/77/download")


@pytest.mark.parametrize(
    "state", [{"status": "failed"}, {"status": "ERROR"}, {"status": "expired"}]
)
def test_export_failed_status_aborts_without_download(state: dict[str, Any]) -> None:
    http = export_http(make_zip(RECORDS), [state])
    with pytest.raises(ExportFailedError):
        client(http).export("u")
    assert not any("download" in r.url for r in http.requests)


@pytest.mark.parametrize(
    "state",
    [
        {"status": "weird", "listen_count": 2},
        {"listen_count": 2},
        {"status": None},
        {"progress": 100},
    ],
)
def test_export_only_explicit_ready_status_downloads(state: dict[str, Any]) -> None:
    http = export_http(make_zip(RECORDS), [state] * 3)
    clock = Clock()
    with pytest.raises(ExportTimeoutError):
        client(http, clock, max_polls=3, poll_interval=1).export("u")
    assert not any("download" in r.url for r in http.requests)
    assert clock.sleeps == [1, 1]  # bounded polling


@pytest.mark.parametrize("bad", [{}, {"export_id": ""}, {"export_id": "../x"}, {"export_id": [1]}])
def test_export_bad_id(bad: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError):
        client(export_http(b"", create=bad)).export("u")


def test_export_5xx_during_lifecycle_raises_after_bounded_retries() -> None:
    http = FakeHttp(
        ok({"valid": True, "user_name": "u"}),
        ok({"payload": {"count": 1}}),
        ok({"export_id": 1}),
        status(502),
    )
    with pytest.raises(HttpStatusError):
        ListenBrainzClient(make(http, max_retries=1)).export("u")


def test_export_empty_always_raises_on_exhaustion() -> None:
    for raw in (b"", b"\n\n"):
        snap = client(
            export_http(make_zip(raw=raw), [{"status": "complete", "listen_count": 0}], count=0)
        ).export("u")
        with pytest.raises(ExportIncompleteError, match="no listens"):
            rows_of(snap)


def test_export_manifest_mismatch_fails_at_exhaustion() -> None:
    snap = client(export_http(make_zip(RECORDS), [{"status": "ready", "listen_count": 3}])).export(
        "u"
    )
    with pytest.raises(ExportIncompleteError, match="manifest says 3"):
        rows_of(snap)
    # extra rows beyond manifest are also a mismatch
    snap = client(export_http(make_zip(RECORDS), [{"status": "ready", "listen_count": 1}])).export(
        "u"
    )
    with pytest.raises(ExportIncompleteError):
        rows_of(snap)


def test_export_pre_count_is_lower_bound_count_alone_not_proof() -> None:
    snap = client(export_http(make_zip(RECORDS), count=3)).export("u")
    with pytest.raises(ExportIncompleteError, match="at least 3"):
        rows_of(snap)
    snap = client(export_http(make_zip(RECORDS), with_count=False)).export("u", min_records=5)
    with pytest.raises(ExportIncompleteError):
        rows_of(snap)
    # explicit lower bound lets a legitimately smaller (post-deletion) export through
    ok_snap = client(export_http(make_zip(RECORDS), count=9, with_count=False)).export(
        "u", min_records=2
    )
    assert rows_of(ok_snap) == RECORDS
    # more rows than the global count (listens arrived meanwhile) is fine without manifest
    snap = client(export_http(make_zip(RECORDS), [{"status": "ready"}], count=1)).export("u")
    assert rows_of(snap) == RECORDS


def test_export_uuid_spellings_cannot_mask_duplicate_identity() -> None:
    first = listen(1)
    alternate = dict(first, recording_msid=first["recording_msid"].upper().replace("-", ""))
    adapter = client(
        export_http(make_zip([first, alternate]), [{"status": "ready", "listen_count": 2}], count=0)
    )
    with pytest.raises(ExportCorruptError, match="duplicate"):
        rows_of(adapter.export("u"))


def test_export_duplicate_identity_rejected() -> None:
    snap = client(
        export_http(make_zip([listen(1), listen(1), listen(2)]), [{"status": "ready"}], count=0)
    ).export("u")
    with pytest.raises(ExportCorruptError, match="duplicate"):
        rows_of(snap)
    # same ts with different msids is NOT a duplicate
    snap = client(
        export_http(make_zip([listen(1, "a"), listen(1, "b")]), [{"status": "ready"}], count=0)
    ).export("u")
    assert len(rows_of(snap)) == 2


def test_export_invalid_identity_rows_yielded_and_counted_toward_manifest() -> None:
    bad = {"listened_at": None, "recording_msid": "x", "track_metadata": {}}
    no_msid = {"listened_at": 5, "track_metadata": {}}
    snap = client(
        export_http(make_zip([listen(1), bad, no_msid]), [{"status": "ready", "listen_count": 3}])
    ).export("u")
    assert snap.complete and snap.expected_count == 3
    # delivered for raw quarantine; the repository is the final identity safety check
    assert rows_of(snap) == [listen(1), bad, no_msid]
    # manifest still enforced over valid + invalid rows
    short = client(
        export_http(make_zip([listen(1), bad]), [{"status": "ready", "listen_count": 3}])
    ).export("u")
    with pytest.raises(ExportIncompleteError, match="manifest says 3"):
        rows_of(short)
    no_manifest = client(
        export_http(make_zip([listen(1), bad]), [{"status": "ready"}], count=0)
    ).export("u")
    assert len(rows_of(no_manifest)) == 2 and not no_manifest.complete


def test_export_corrupt_zip_crc_detected_only_at_eof_streaming() -> None:
    blob = bytearray(make_zip(RECORDS))
    blob[blob.index(b"recording_msid")] ^= 0x01  # stored data altered, CRC mismatch
    snap = client(export_http(bytes(blob))).export("u")
    with pytest.raises(ExportCorruptError):
        rows_of(snap)


def test_export_truncated_or_non_zip_fails_eagerly() -> None:
    blob = make_zip(RECORDS)
    for bad in (blob[: len(blob) // 2], b"<html>nope</html>", b""):
        with pytest.raises(ExportCorruptError):
            client(export_http(bad)).export("u")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"listened_at": 1}\n{"listened_at": 2\n',  # truncated line
        b'{"a": 1}\n[1, 2]\n',  # not an object
        b'{"a": 1}\n"str"\n',
        b'{"a": 1}\n\xff\xfe\n',  # undecodable
        b"not json\n",
    ],
)
def test_export_invalid_jsonl_fails_on_exhaustion(raw: bytes) -> None:
    snap = client(export_http(make_zip(raw=raw), [{"status": "ready"}], count=0)).export("u")
    with pytest.raises(ExportCorruptError):
        rows_of(snap)


def test_export_archive_without_listens_members_is_incomplete_not_imported() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("user.json", json.dumps({"user_id": 1, "username": "u"}))
        zf.writestr("feedback.jsonl", '{"a":1}\n')
        zf.writestr("listens.jsonl", '{"a":1}\n')  # old guessed flat name: ignored
        zf.writestr("notes.txt", "x")
    snap = client(export_http(buf.getvalue(), count=0)).export("u")
    with pytest.raises(ExportIncompleteError, match="no listens"):
        rows_of(snap)


@pytest.mark.parametrize(
    "doc",
    [None, b"not json", b"[]", b"{}", b'{"username": ""}', b'{"username": 5}', b"\xff"],
)
def test_export_user_json_missing_or_unreadable_never_verifies(doc: bytes | None) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("listens/2026/01.jsonl", json.dumps(RECORDS[0]) + "\n")
        if doc is not None:
            zf.writestr("user.json", doc)
    with pytest.raises(ExportError):
        client(export_http(buf.getvalue(), count=0)).export("u")


def test_export_size_limits_checked_before_decompression_or_crc() -> None:
    blob = make_zip(RECORDS)
    with pytest.raises(ExportTooLargeError):
        client(export_http(blob), max_zip_bytes=10).export("u")
    with pytest.raises(ExportCorruptError):
        client(export_http(blob), max_unzipped_bytes=10).export("u")
    with pytest.raises(ExportCorruptError):
        client(export_http(blob), max_members=1).export("u")


def test_oversized_unselected_member_and_lying_archive_rejected_pre_read() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("user.json", json.dumps({"username": "u"}))
        zf.writestr("listens/2026/01.jsonl", json.dumps(listen(1)) + "\n")
        zf.writestr("big.bin", b"\0" * (5 << 20))  # compresses to ~5 KiB
    blob = buf.getvalue()
    assert len(blob) < 100_000
    with pytest.raises(ExportCorruptError, match="uncompressed"):
        client(export_http(blob), max_unzipped_bytes=1 << 20).export("u")


def test_export_rows_stream_and_close() -> None:
    snap = client(export_http(make_zip(RECORDS))).export("u")
    it = iter(snap.rows)
    assert next(it) == RECORDS[0]
    it.close()  # type: ignore[attr-defined]  # abandoned stream must not raise


def test_errors_never_contain_token() -> None:
    http = export_http(make_zip(RECORDS), [{"status": "failed", "error": TOKEN}])
    with pytest.raises(ExportFailedError) as err:
        client(http).export("u")
    assert TOKEN not in str(err.value)
