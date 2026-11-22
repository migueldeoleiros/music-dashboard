"""Public refresh route: gating, same-origin, headers, SQL shape. Fake engine, no database."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from music_dashboard.app import app
from music_dashboard.config import settings
from music_dashboard.presentation.api.routes import refresh as route

client = TestClient(app, base_url="http://music.test:8080")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeMappings:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class FakeConn:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        self.engine.log.append("BEGIN")
        return self

    def exec_driver_sql(self, sql):
        self.engine.log.append(sql)

    def execute(self, statement, params=None):
        self.engine.log.append(str(statement))
        self.engine.params.append(params)
        if self.engine.error:
            raise self.engine.error
        return FakeMappings(self.engine.row)


class FakeEngine:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.log, self.params = row, error, [], []

    def connect(self):
        return FakeConn(self)


def row(status="pending", coalesced=False, **kw):
    base = {
        "request_id": uuid4(),
        "status": status,
        "requested_at": NOW,
        "started_at": None,
        "finished_at": None,
        "cooldown_until": NOW + timedelta(seconds=300),
        "retry_at": None,
        "detail": None,
        "coalesced": coalesced,
        "server_now": NOW,
    }
    return base | kw


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "public_refresh_enabled", True)
    monkeypatch.setattr(settings, "fixture_mode", False)


def install(monkeypatch, **kw):
    engine = FakeEngine(**kw)
    monkeypatch.setattr(route, "refresh_engine", lambda: engine)
    return engine


def boom(*_):
    raise AssertionError("database must not be touched")


def test_disabled_by_default_and_never_touches_database(monkeypatch):
    monkeypatch.setattr(route, "refresh_engine", boom)
    got = client.get("/api/refresh")
    assert got.status_code == 200
    assert got.json()["enabled"] is False and got.json()["status"] == "idle"
    assert got.headers["cache-control"] == "no-store"
    posted = client.post("/api/refresh", headers={"Origin": "http://music.test:8080"})
    assert posted.status_code == 404
    assert posted.headers["cache-control"] == "no-store"


def test_enabled_but_fixture_mode_stays_disabled(monkeypatch):
    monkeypatch.setattr(settings, "public_refresh_enabled", True)
    monkeypatch.setattr(settings, "fixture_mode", True)
    monkeypatch.setattr(route, "refresh_engine", boom)
    assert client.get("/api/refresh").json()["enabled"] is False
    assert client.post("/api/refresh").status_code == 404


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "http://evil.test:8080",
        "http://music.test",  # port dropped
        "https://music.test:8080",  # scheme differs
        "http://music.test:8080/path",
        "http://user@music.test:8080",
        "http://[invalid",  # urlsplit raises ValueError for malformed IPv6 authorities
        "http://[not-an-ipv6-address]",
    ],
)
def test_cross_origin_rejected_before_database(enabled, monkeypatch, origin):
    monkeypatch.setattr(route, "refresh_engine", boom)
    headers = {"Origin": origin} if origin else {}
    res = client.post("/api/refresh", headers=headers)
    assert res.status_code == 403
    assert res.headers["cache-control"] == "no-store"


def test_sec_fetch_site_cross_site_rejected(enabled, monkeypatch):
    monkeypatch.setattr(route, "refresh_engine", boom)
    res = client.post(
        "/api/refresh",
        headers={"Origin": "http://music.test:8080", "Sec-Fetch-Site": "cross-site"},
    )
    assert res.status_code == 403


def test_default_port_normalised(enabled, monkeypatch):
    install(monkeypatch, row=row())
    plain = TestClient(app, base_url="http://music.test")
    assert plain.post("/api/refresh", headers={"Origin": "http://music.test:80"}).status_code == 202


def test_admission_is_isolated_read_write_transaction(enabled, monkeypatch):
    engine = install(monkeypatch, row=row())
    res = client.post("/api/refresh", headers={"Origin": "http://music.test:8080"})
    assert res.status_code == 202
    assert res.headers["cache-control"] == "no-store"
    assert engine.log[:2] == ["BEGIN", "SET TRANSACTION READ WRITE"]
    assert "public_refresh_request" in engine.log[2]
    body = res.json()
    assert body["enabled"] is True and body["status"] == "pending" and body["coalesced"] is False
    assert body["retry_after_seconds"] == 300 and body["request_id"]
    assert engine.params[0] is None  # fixed server-side policy: caller supplies nothing


def test_coalesced_flag_and_cooldown_replay(enabled, monkeypatch):
    install(monkeypatch, row=row("running", coalesced=True))
    res = client.post("/api/refresh", headers={"Origin": "http://music.test:8080"})
    assert res.status_code == 202 and res.json()["coalesced"] is True
    install(monkeypatch, row=row("succeeded", finished_at=NOW))
    res = client.post("/api/refresh", headers={"Origin": "http://music.test:8080"})
    assert res.status_code == 200 and res.json()["status"] == "succeeded"


def test_get_reads_through_definer_function_only(enabled, monkeypatch):
    engine = install(monkeypatch, row=row("deferred", detail="requires_reconcile"))
    res = client.get("/api/refresh")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert res.json()["detail"] == "requires_reconcile"
    assert engine.log == ["SELECT * FROM public.public_refresh_status()"]


def test_get_no_row_is_idle(enabled, monkeypatch):
    install(monkeypatch, row=None)
    assert client.get("/api/refresh").json()["status"] == "idle"


def test_database_error_maps_to_503(enabled, monkeypatch):
    from sqlalchemy.exc import OperationalError

    install(monkeypatch, error=OperationalError("s", {}, Exception("secret-dsn")))
    for res in (
        client.get("/api/refresh"),
        client.post("/api/refresh", headers={"Origin": "http://music.test:8080"}),
    ):
        assert res.status_code == 503
        assert res.headers["cache-control"] == "no-store"
        assert "secret" not in res.text
