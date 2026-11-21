import io
import json
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any

import pytest

from music_dashboard.infrastructure.listenbrainz.listens import (
    ExportCorruptError,
    ExportFailedError,
    ExportIncompleteError,
    ExportTimeoutError,
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
        "recording_msid": msid,
        "track_metadata": {"track_name": "T", "additional_info": {"x": [1, {"y": None}]}},
        "mbid_mapping": {"recording_mbid": "r"},
    }


def make_zip(
    records: list[Any] | None = None, name: str = "listens.jsonl", raw: bytes | None = None
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        data = raw if raw is not None else "".join(json.dumps(r) + "\n" for r in records or [])
        zf.writestr(name, data)
        zf.writestr("user.json", "{}")
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
    http = FakeHttp(status(429, {"Retry-After": "7"}), status(429, {"retry-after": "9999"}), ok({}))
    make(http, clock, max_backoff=30).request("GET", "/1/x")
    assert clock.sleeps == [7, 30]
    assert len(http.requests) == 3


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


def export_http(
    blob: bytes,
    states: list[dict[str, Any]] | None = None,
    count: int = 2,
    create: dict[str, Any] | None = None,
    with_count: bool = True,
) -> FakeHttp:
    states = states if states is not None else [{"status": "complete"}]
    seq: list[HttpResponse] = [
        *([ok({"payload": {"count": count}})] if with_count else []),
        ok(create if create is not None else {"export_id": 77}),
        *[ok(s) for s in states],
        zip_ok(blob),
    ]
    return FakeHttp(*seq)


RECORDS = [listen(1), listen(1, "m2")]


def test_export_happy_path_synthetic_shape_and_calls() -> None:
    http = export_http(
        make_zip(RECORDS),
        [{"status": "waiting"}, {"status": "in_progress"}, {"status": "complete"}],
    )
    clock = Clock()
    got = client(http, clock, poll_interval=2).export("u")
    assert got == RECORDS  # metadata preserved verbatim
    calls = [(r.method, r.url.split(".org")[1]) for r in http.requests]
    assert calls == [
        ("GET", "/1/user/u/listen-count"),
        ("POST", "/1/export/"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/77"),
        ("GET", "/1/export/download/77"),
    ]
    assert all("Authorization" in r.headers for r in http.requests[1:])
    assert "Authorization" not in http.requests[0].headers
    assert clock.sleeps == [2, 2]


def test_export_documented_alt_shapes() -> None:
    http = export_http(
        make_zip(RECORDS, name="export/listens.jsonl"),
        [{"status": "Completed", "download_url": "https://api.listenbrainz.org/dl/77.zip"}],
        create={"id": "abc-1"},
    )
    assert client(http).export("u") == RECORDS
    assert http.requests[-1].url == "https://api.listenbrainz.org/dl/77.zip"
    assert http.requests[2].url.endswith("/1/export/abc-1")
    # single unnamed jsonl member + "ready"
    assert (
        client(export_http(make_zip(RECORDS, name="dump.jsonl"), [{"status": "ready"}])).export("u")
        == RECORDS
    )


def test_export_foreign_download_url_rejected_before_download() -> None:
    http = export_http(
        make_zip(RECORDS), [{"status": "complete", "download_url": "https://evil.test/z"}]
    )
    with pytest.raises(ConfigurationError):
        client(http).export("u")
    assert all("evil" not in r.url for r in http.requests)


@pytest.mark.parametrize(
    "state", [{"status": "failed"}, {"status": "ERROR"}, {"status": "expired"}]
)
def test_export_failed_status_aborts_without_download(state: dict[str, Any]) -> None:
    http = export_http(make_zip(RECORDS), [state])
    with pytest.raises(ExportFailedError):
        client(http).export("u")
    assert not any("download" in r.url for r in http.requests)


@pytest.mark.parametrize("state", [{"status": "weird"}, {}, {"status": None}, {"progress": 100}])
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
    http = FakeHttp(ok({"payload": {"count": 1}}), ok({"export_id": 1}), status(502))
    with pytest.raises(HttpStatusError):
        ListenBrainzClient(make(http, max_retries=1)).export("u")


def test_export_empty_always_raises() -> None:
    with pytest.raises(ExportIncompleteError, match="no listens"):
        client(export_http(make_zip([]), count=0)).export("u")
    with pytest.raises(ExportIncompleteError):
        client(export_http(make_zip(raw=b"\n\n"), with_count=False)).export("u", min_records=0)


def test_export_fewer_than_expected_records_raises_count_alone_not_enough() -> None:
    with pytest.raises(ExportIncompleteError, match="at least 3"):
        client(export_http(make_zip(RECORDS), count=3)).export("u")
    with pytest.raises(ExportIncompleteError):
        client(export_http(make_zip(RECORDS), with_count=False)).export("u", min_records=5)
    # explicit lower bound lets a legitimately smaller (post-deletion) export through
    assert (
        client(export_http(make_zip(RECORDS), with_count=False)).export("u", min_records=2)
        == RECORDS
    )
    # more records than the count (listens arrived meanwhile) is fine
    assert client(export_http(make_zip(RECORDS), count=1)).export("u") == RECORDS


def test_export_corrupt_zip_crc() -> None:
    blob = bytearray(make_zip(RECORDS))
    marker = blob.index(b"recording_msid")
    blob[marker] ^= 0x01  # stored data altered, CRC no longer matches
    with pytest.raises(ExportCorruptError):
        client(export_http(bytes(blob))).export("u")


def test_export_truncated_or_non_zip() -> None:
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
def test_export_invalid_jsonl_all_or_nothing(raw: bytes) -> None:
    with pytest.raises(ExportCorruptError):
        client(export_http(make_zip(raw=raw), count=0)).export("u")


def test_export_zip_without_listens_member_or_ambiguous() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.jsonl", '{"a":1}\n')
        zf.writestr("b.jsonl", '{"b":1}\n')
    with pytest.raises(ExportCorruptError):
        client(export_http(buf.getvalue(), count=0)).export("u")
    with pytest.raises(ExportCorruptError):
        client(export_http(make_zip(raw=b"", name="notes.txt"), count=0)).export("u")


def test_export_size_limits() -> None:
    blob = make_zip(RECORDS)
    with pytest.raises(ExportCorruptError):
        client(export_http(blob), max_zip_bytes=10).export("u")
    with pytest.raises(ExportCorruptError):
        client(export_http(blob), max_unzipped_bytes=10).export("u")


def test_errors_never_contain_token() -> None:
    http = export_http(make_zip(RECORDS), [{"status": "failed", "error": TOKEN}])
    with pytest.raises(ExportFailedError) as err:
        client(http).export("u")
    assert TOKEN not in str(err.value)
    assert isinstance(urllib.request, object)
