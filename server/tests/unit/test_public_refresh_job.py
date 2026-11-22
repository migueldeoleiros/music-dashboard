"""Worker public refresh: gates, incremental-only, recovery. No network, no database."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_sync_application import MemoryRepository

from music_dashboard.application.jobs import refresh as refresh_job
from music_dashboard.application.jobs import sync as job
from music_dashboard.application.sync_listens import BoundaryAmbiguity, SyncBusy
from music_dashboard.infrastructure.persistence.repositories.refresh import RefreshRequest


class FakeRefresh:
    def __init__(self, active=True):
        self.active = active
        self.rid = uuid4()
        self.calls = []

    def has_active(self):
        return self.active

    def claim(self, now=None):
        self.calls.append(("claim",))
        if not self.active:
            return None
        return RefreshRequest(self.rid, "pending", None, datetime.now(UTC))

    def mark_running(self, rid, now=None):
        self.calls.append(("running",))
        return True

    def mark_deferred(self, rid, detail, retry_at=None):
        self.calls.append(("deferred", detail))

    def finish(self, rid, status, detail=None, retry_at=None, now=None):
        self.calls.append(("finish", status, detail))


@pytest.fixture
def runtime(monkeypatch):
    from music_dashboard.infrastructure.listenbrainz import listens, transport
    from music_dashboard.infrastructure.persistence import database
    from music_dashboard.infrastructure.persistence.repositories import listening_sync
    from music_dashboard.infrastructure.persistence.repositories import refresh as refresh_repo

    monkeypatch.setenv("MUSIC_PUBLIC_REFRESH_ENABLED", "true")
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", "synthetic-user")
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "synthetic-test-token")
    repo, fake = MemoryRepository(), FakeRefresh()
    now = datetime.now(UTC)
    repo.state = {"last_export_at": now, "last_incremental_at": now}
    engine = Mock()
    monkeypatch.setattr(database, "create_db_engine", lambda: engine)
    monkeypatch.setattr(listening_sync, "SyncRepository", lambda e: repo)
    monkeypatch.setattr(refresh_repo, "RefreshRepository", lambda e: fake)
    monkeypatch.setattr(listens, "ListenBrainzClient", lambda transport, **kw: Mock())
    monkeypatch.setattr(transport, "UrllibHttpTransport", Mock())
    incremental, reconcile = Mock(return_value={"changed": 1}), Mock()
    monkeypatch.setattr(job, "sync_listens", incremental)
    monkeypatch.setattr(job, "reconcile_history", reconcile)
    return repo, fake, engine, incremental, reconcile


def test_idle_does_nothing(runtime):
    repo, fake, engine, incremental, reconcile = runtime
    fake.active = False
    assert job.run_public_refresh() == {"status": "idle"}
    assert fake.calls == []
    incremental.assert_not_called()
    engine.dispose.assert_called_once()


def test_success_runs_incremental_only(runtime):
    repo, fake, _, incremental, reconcile = runtime
    assert job.run_public_refresh()["status"] == "succeeded"
    assert fake.calls == [("claim",), ("running",), ("finish", "succeeded", None)]
    incremental.assert_called_once()
    reconcile.assert_not_called()


def test_runs_even_when_scheduled_interval_not_due(runtime):
    repo, fake, _, incremental, _ = runtime
    repo.state["last_incremental_at"] = datetime.now(UTC)
    assert job.run_public_refresh()["status"] == "succeeded"


@pytest.mark.parametrize(
    ("setup", "detail"),
    [
        (lambda r: r.state.update(requires_reconcile=True), "requires_reconcile"),
        (lambda r: r.state.update(last_export_at=None), "awaiting_initial_export"),
        (
            lambda r: setattr(r, "retry", {"incremental": datetime.now(UTC) + timedelta(hours=1)}),
            "retry_scheduled",
        ),
    ],
)
def test_closed_gates_defer_and_never_export(runtime, setup, detail):
    repo, fake, _, incremental, reconcile = runtime
    setup(repo)
    assert job.run_public_refresh() == {"status": "deferred"}
    assert ("deferred", detail) in fake.calls
    incremental.assert_not_called()
    reconcile.assert_not_called()


def test_active_rate_limit_defers(runtime):
    repo, fake, _, incremental, reconcile = runtime
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    repo.runs.append(
        {"kind": "incremental", "status": "failed", "stats": {"rate_limit_until": until}}
    )
    assert job.run_public_refresh() == {"status": "deferred"}
    assert ("deferred", "rate_limited") in fake.calls
    incremental.assert_not_called()


def test_lock_held_leaves_request_untouched(runtime):
    repo, fake, _, incremental, _ = runtime
    repo.acquire = False
    assert job.run_public_refresh() == {"status": "busy"}
    assert fake.calls == []
    incremental.assert_not_called()


def test_failure_marks_failed_without_raising_or_leaking(runtime):
    repo, fake, _, incremental, _ = runtime
    incremental.side_effect = RuntimeError("token synthetic-test-token")
    assert job.run_public_refresh() == {"status": "failed"}
    assert fake.calls[-1] == ("finish", "failed", "sync_failed")


def test_ambiguity_and_busy_defer(runtime):
    repo, fake, _, incremental, _ = runtime
    incremental.side_effect = BoundaryAmbiguity("x")
    assert job.run_public_refresh() == {"status": "deferred"}
    assert ("deferred", "requires_reconcile") in fake.calls
    incremental.side_effect = SyncBusy("x")
    assert job.run_public_refresh() == {"status": "deferred"}


def test_fixture_mode_fails_closed_without_upstream(runtime, monkeypatch):
    repo, fake, _, incremental, _ = runtime
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    assert job.run_public_refresh() == {"status": "failed"}
    assert fake.calls[-1] == ("finish", "failed", "fixture_mode")
    incremental.assert_not_called()


def test_registry_tick_delegates(monkeypatch):
    spy = Mock()
    monkeypatch.setattr(refresh_job, "run_public_refresh", spy)
    refresh_job.tick()
    spy.assert_called_once_with()


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "garbage"])
def test_disabled_by_default_touches_nothing(monkeypatch, value):
    from music_dashboard.infrastructure.persistence import database

    for name in ("LISTENBRAINZ_USERNAME", "LISTENBRAINZ_TOKEN", "MUSIC_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")  # would raise config error if parsed
    if value is None:
        monkeypatch.delenv("MUSIC_PUBLIC_REFRESH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MUSIC_PUBLIC_REFRESH_ENABLED", value)
    forbidden = Mock(side_effect=AssertionError("database must not be touched"))
    monkeypatch.setattr(database, "create_db_engine", forbidden)
    assert job.run_public_refresh() == {"status": "disabled"}
    refresh_job.tick()  # registry tick is a silent no-op too
    forbidden.assert_not_called()
