"""Registry scheduling and credential-safe CLI errors; no network or database calls."""

import argparse
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from test_sync_application import MemoryRepository

from music_dashboard.application.jobs import sync as job
from music_dashboard.application.sync_listens import SyncBusy
from music_dashboard.presentation.cli_commands import sync as cli


@pytest.fixture
def runtime(monkeypatch):
    from music_dashboard.infrastructure.listenbrainz import listens, transport
    from music_dashboard.infrastructure.persistence import database
    from music_dashboard.infrastructure.persistence.repositories import listening_sync

    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", "synthetic-user")
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "synthetic-test-token")
    repo = MemoryRepository()
    now = datetime.now(UTC)
    repo.state = {"last_export_at": now, "last_incremental_at": now}
    engine = Mock()
    monkeypatch.setattr(database, "create_db_engine", lambda: engine)
    monkeypatch.setattr(listening_sync, "SyncRepository", lambda engine: repo)
    monkeypatch.setattr(listens, "ListenBrainzClient", lambda transport: Mock())
    monkeypatch.setattr(transport, "UrllibHttpTransport", Mock())
    incremental, reconcile = (
        Mock(return_value={"mode": "incremental"}),
        Mock(return_value={"mode": "reconcile"}),
    )
    monkeypatch.setattr(job, "sync_listens", incremental)
    monkeypatch.setattr(job, "reconcile_history", reconcile)
    return repo, engine, incremental, reconcile


def test_not_due_does_not_dispatch(runtime):
    repo, engine, incremental, reconcile = runtime
    assert job.run_sync("incremental", scheduled=True) == {"status": "not-due"}
    incremental.assert_not_called()
    reconcile.assert_not_called()
    engine.dispose.assert_called_once()


def test_fifteen_minute_incremental_interval(runtime):
    repo, engine, incremental, reconcile = runtime
    repo.state["last_incremental_at"] -= timedelta(minutes=16)
    assert job.run_sync("incremental", scheduled=True) == {"mode": "incremental"}
    assert incremental.call_args.kwargs["overlap"] == timedelta(hours=72)
    reconcile.assert_not_called()


@pytest.mark.parametrize("state", ["weekly", "bootstrap", "ambiguous"])
def test_weekly_bootstrap_and_ambiguity_use_exports(runtime, state):
    repo, engine, incremental, reconcile = runtime
    if state == "weekly":
        repo.state["last_export_at"] -= timedelta(days=8)
    elif state == "bootstrap":
        repo.state["last_export_at"] = None
    else:
        repo.state["requires_reconcile"] = True
    assert job.run_sync("incremental", scheduled=True) == {"mode": "reconcile"}
    assert reconcile.call_args.kwargs["allow_large_delete"] is False
    incremental.assert_not_called()


def test_persistent_backoff_prevents_dispatch_but_explicit_cli_can_retry(runtime):
    repo, engine, incremental, reconcile = runtime
    repo.state["last_incremental_at"] -= timedelta(minutes=20)
    repo.retry = {"incremental": datetime.now(UTC) + timedelta(minutes=10)}
    assert job.run_sync("incremental", scheduled=True) == {"status": "backoff"}
    assert job.run_sync("incremental", scheduled=False) == {"mode": "incremental"}
    assert incremental.call_count == 1
    reconcile.assert_not_called()


def test_failed_weekly_reconcile_does_not_starve_incremental(runtime):
    repo, engine, incremental, reconcile = runtime
    repo.state["last_export_at"] -= timedelta(days=8)
    repo.state["last_incremental_at"] -= timedelta(minutes=20)
    repo.retry = {"reconcile": datetime.now(UTC) + timedelta(minutes=10)}
    assert job.run_sync("incremental", scheduled=True) == {"mode": "incremental"}
    reconcile.assert_not_called()


def test_ambiguity_still_blocks_incremental_during_export_backoff(runtime):
    repo, engine, incremental, reconcile = runtime
    repo.state["requires_reconcile"] = True
    repo.retry = {"reconcile": datetime.now(UTC) + timedelta(minutes=10)}
    assert job.run_sync("incremental", scheduled=True) == {"status": "backoff"}
    incremental.assert_not_called()
    reconcile.assert_not_called()


def test_worker_schedule_checks_are_lock_guarded(runtime):
    repo, engine, incremental, reconcile = runtime
    repo.acquire = False
    with pytest.raises(SyncBusy):
        job.run_sync("incremental", scheduled=True)
    incremental.assert_not_called()
    reconcile.assert_not_called()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("failed_kind", ["incremental", "reconcile"])
def test_long_upstream_rate_limit_survives_restart_and_blocks_both_jobs(runtime, failed_kind):
    repo, engine, incremental, reconcile = runtime
    repo.state["last_export_at"] -= timedelta(days=8)
    repo.state["last_incremental_at"] -= timedelta(minutes=20)
    repo.runs.append(
        {
            "kind": failed_kind,
            "status": "failed",
            "stats": {
                "rate_limit_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        }
    )
    assert job.run_sync("incremental", scheduled=True) == {"status": "backoff"}
    assert job.run_sync("reconcile", scheduled=True) == {"status": "backoff"}
    with pytest.raises(SyncBusy):
        job.run_sync("reconcile", scheduled=False)
    incremental.assert_not_called()
    reconcile.assert_not_called()


def test_configurable_schedule_and_overlap(runtime, monkeypatch):
    repo, engine, incremental, reconcile = runtime
    monkeypatch.setenv("MUSIC_SYNC_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("MUSIC_SYNC_OVERLAP_HOURS", "24")
    repo.state["last_incremental_at"] -= timedelta(seconds=2)
    job.run_sync("incremental", scheduled=True)
    assert incremental.call_args.kwargs["overlap"] == timedelta(hours=24)


def test_cli_never_echoes_unknown_exception_or_credentials(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ValueError("synthetic-test-token in an unsafe upstream exception")

    monkeypatch.setattr(cli, "run_sync", fail)
    with pytest.raises(SystemExit) as error:
        cli._handle(argparse.Namespace(sync_kind="incremental", allow_large_delete=False))
    assert "synthetic-test-token" not in str(error.value)
    assert "synthetic-test-token" not in capsys.readouterr().err


def test_all_cli_commands_discovered_and_override_only_export():
    parser = argparse.ArgumentParser()
    cli.configure(parser.add_subparsers())
    for command in ("bootstrap-import", "sync", "reconcile"):
        args = parser.parse_args([command])
        assert args.handler is cli._handle
        assert not args.allow_large_delete
    assert parser.parse_args(["reconcile", "--allow-large-delete"]).allow_large_delete
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--allow-large-delete"])
