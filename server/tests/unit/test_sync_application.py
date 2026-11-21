"""Synthetic ports exercise paging/checkpoint semantics without any upstream calls."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from music_dashboard.application.jobs.sync import SyncConfigurationError, WorkerSyncSettings
from music_dashboard.application.reconcile_history import reconcile_history
from music_dashboard.application.sync_listens import BoundaryAmbiguity, SyncBusy, sync_listens
from music_dashboard.domain.listening.entities import ExportSnapshot

NOW = datetime(2026, 1, 10, tzinfo=UTC)
T = int(NOW.timestamp()) - 60


def row(at=T, n=1, **values):
    return {"listened_at": at, "recording_msid": str(UUID(int=n)), **values}


class MemoryRepository:
    def __init__(self):
        self.state = {}
        self.data = {}
        self.runs = []
        self.checkpoints = []
        self.activation_calls = 0
        self.acquire = True
        self.retry = None

    @contextmanager
    def locked(self, user):
        yield self.acquire

    def load_checkpoint(self, user):
        return self.state.copy()

    def save_checkpoint(self, user, state):
        self.state.update(state)
        self.checkpoints.append(state)

    def begin_run(self, user, kind, stats=None):
        self.runs.append({"kind": kind, "status": "running", "stats": stats or {}})
        return len(self.runs) - 1

    def finish_run(self, run_id, status, stats, error=None):
        self.runs[run_id].update(status=status, stats=stats, error=error)

    def ingest(self, user, payloads, source, received_at):
        changed = 0
        for payload in payloads:
            key = (user, payload.get("listened_at"), payload.get("recording_msid"))
            changed += self.data.get(key) != payload
            self.data[key] = payload
        return changed

    def load_scan(self, user):
        for run in reversed(self.runs):
            if run["kind"] == "incremental":
                return run["stats"].get("scan") if run["status"] in {"running", "failed"} else None
        return None

    def activate_snapshot(
        self, user, rows, snapshot_at, allow_large_delete=False, snapshot_complete=False
    ):
        self.activation_calls += 1
        return {"changed": self.ingest(user, list(rows), "export", snapshot_at)}

    def next_retry(self, user, kind=None):
        return self.retry.get(kind) if isinstance(self.retry, dict) else self.retry


class Pages:
    def __init__(self, rows):
        self.rows = rows
        self.bounds = []
        self.fail_on = None

    def page(self, user, *, max_ts=None, min_ts=None, count=1000):
        self.bounds.append(max_ts)
        if self.fail_on == len(self.bounds):
            raise RuntimeError("a private upstream error")
        return sorted(
            [r for r in self.rows if r["listened_at"] < max_ts],
            key=lambda r: r["listened_at"],
            reverse=True,
        )[:count]

    def export(self, user):
        return ExportSnapshot(self.rows, user, len(self.rows), complete=True)


def test_incremental_rerun_idempotent_and_distinct_msids():
    repo = MemoryRepository()
    source = Pages([row(n=1), row(n=2)])
    assert sync_listens(source, repo, "u", now=NOW)["changed"] == 2
    assert sync_listens(source, repo, "u", now=NOW)["changed"] == 0
    assert len(repo.data) == 2
    assert repo.state["last_incremental_at"] == NOW
    assert repo.state["cursor_max_ts"] is None


def test_boundary_refetch_clears_ties_before_exclusive_progress():
    # 2 newer + 999 at T means the first page truncates T. Refetch reveals all
    # 999 plus an older row; it is full but NOT all at T, so the boundary is safe.
    rows = [row(T + 2, 1), row(T + 1, 2)]
    rows += [row(T, n) for n in range(3, 1002)]
    rows += [row(T - 1, 1002)]
    repo, source = MemoryRepository(), Pages(rows)
    sync_listens(source, repo, "u", now=NOW)
    assert len(repo.data) == 1002
    assert source.bounds[:3] == [int(NOW.timestamp()) + 1, T + 1, T]
    assert repo.checkpoints[0] == {"cursor_max_ts": T}


@pytest.mark.parametrize("ties", [1000, 1001, 1500])
def test_all_same_second_is_ambiguous_never_checkpoint_complete(ties):
    repo = MemoryRepository()
    with pytest.raises(BoundaryAmbiguity, match="reconcile"):
        sync_listens(Pages([row(T, n) for n in range(1, ties + 1)]), repo, "u", now=NOW)
    assert repo.state["requires_reconcile"] is True
    assert repo.state["ambiguous_boundary_ts"] == T
    assert "last_incremental_at" not in repo.state
    assert not any("cursor_max_ts" in checkpoint for checkpoint in repo.checkpoints)
    assert repo.runs[-1]["status"] == "ambiguous"
    assert repo.runs[-1]["stats"]["next_retry_at"]
    with pytest.raises(BoundaryAmbiguity):
        sync_listens(Pages([]), repo, "u", now=NOW)


def test_overlap_late_arrival_caught_old_data_export_only():
    repo, source = MemoryRepository(), Pages([row()])
    sync_listens(source, repo, "u", now=NOW)
    source.rows += [row(T - 3600, 2), row(T - 4 * 86400, 3)]
    sync_listens(source, repo, "u", now=NOW + timedelta(minutes=15))
    assert len(repo.data) == 2
    reconcile_history(source, repo, "u", now=NOW)
    assert len(repo.data) == 3
    assert repo.state["requires_reconcile"] is False


def test_export_and_api_identity_equal_and_remaps_update_not_duplicate():
    repo = MemoryRepository()
    sync_listens(Pages([row()]), repo, "u", now=NOW)
    reconcile_history(Pages([row(track_metadata={"track_name": "remapped"})]), repo, "u", now=NOW)
    assert len(repo.data) == 1
    assert next(iter(repo.data.values()))["track_metadata"]["track_name"] == "remapped"


def test_restart_resumes_only_cleared_boundary_and_replays_idempotently():
    rows = [row(T + n, n + 2000) for n in range(1, 1001)] + [row(T - 1, 1)]
    source, repo = Pages(rows), MemoryRepository()
    source.fail_on = 3  # page + boundary saved, then the next page errors
    with pytest.raises(RuntimeError):
        sync_listens(source, repo, "u", now=NOW + timedelta(hours=1))
    saved = repo.state["cursor_max_ts"]
    assert "last_incremental_at" not in repo.state
    assert repo.runs[-1]["error"] == "RuntimeError"  # not private exception text
    resumed = Pages(rows)
    sync_listens(resumed, repo, "u", now=NOW + timedelta(hours=1))
    assert resumed.bounds[0] == saved
    assert len(repo.data) == len(rows)
    assert repo.state["cursor_max_ts"] is None


@pytest.mark.parametrize("failure", ["partial", "empty", "bad-object", "non-json"])
def test_failed_or_empty_export_never_activates(failure):
    class Broken:
        def export(self, user):
            if failure == "empty":
                return
            yield row()
            if failure == "partial":
                raise RuntimeError("HTTP 503")
            if failure == "bad-object":
                yield [1]
            else:
                yield row(value=float("nan"))

    repo = MemoryRepository()
    with pytest.raises((RuntimeError, ValueError)):
        reconcile_history(Broken(), repo, "u", now=NOW)
    assert repo.activation_calls == 0
    assert not repo.state
    assert repo.runs[-1]["status"] == "failed"


@pytest.mark.parametrize("operation", [sync_listens, reconcile_history])
def test_single_worker_lock_prevents_any_run(operation):
    repo = MemoryRepository()
    repo.acquire = False
    with pytest.raises(SyncBusy):
        operation(Pages([row()]), repo, "u", now=NOW)
    assert not repo.runs
    assert not repo.data


def test_missing_real_credentials_actionable_fixture_token_not_required(monkeypatch):
    monkeypatch.delenv("LISTENBRAINZ_USERNAME", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_TOKEN", raising=False)
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    with pytest.raises(SyncConfigurationError, match="LISTENBRAINZ_USERNAME.*LISTENBRAINZ_TOKEN"):
        WorkerSyncSettings.from_env()
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    config = WorkerSyncSettings.from_env()
    assert config.fixture_mode
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "test-secret-do-not-print")
    assert "test-secret-do-not-print" not in repr(WorkerSyncSettings.from_env())


def test_fixture_worker_tick_does_not_need_db_or_token(monkeypatch):
    from music_dashboard.application.jobs.sync import run_sync

    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    assert run_sync("incremental", scheduled=True) == {"status": "fixture-idle"}


@pytest.mark.parametrize(
    "key,value",
    [
        ("MUSIC_SYNC_OVERLAP_HOURS", "nan"),
        ("MUSIC_SYNC_OVERLAP_HOURS", "inf"),
        ("MUSIC_SYNC_INTERVAL_SECONDS", "0"),
        ("MUSIC_RECONCILE_INTERVAL_SECONDS", "oops"),
    ],
)
def test_bad_schedule_config_sanitized(monkeypatch, key, value):
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    monkeypatch.setenv(key, value)
    with pytest.raises(SyncConfigurationError):
        WorkerSyncSettings.from_env()


def test_long_restart_preserves_original_window_and_next_head_scan():
    # Interrupted first scan near its head, with an unscanned row near its floor.
    started = NOW + timedelta(hours=1)
    bottom = int((started - timedelta(hours=72)).timestamp()) + 1
    rows = [row(T + n, n + 2000) for n in range(1, 1001)] + [row(bottom, 1)]
    first, repo = Pages(rows), MemoryRepository()
    first.fail_on = 3
    with pytest.raises(RuntimeError):
        sync_listens(first, repo, "u", now=started)
    # Add an arrival ABOVE the descending cursor during a week-long outage.
    arrival = row(int((started + timedelta(days=1)).timestamp()), 9000)
    rows.append(arrival)
    restart = started + timedelta(days=7)
    sync_listens(Pages(rows), repo, "u", now=restart)
    assert repo.state["last_incremental_at"] == started  # never advance to restart time
    assert ("u", bottom, str(UUID(int=1))) in repo.data
    # The successful resume may leave a newer arrival above the cursor; next scan
    # must still reach it even though it is now older than 72h relative to restart.
    sync_listens(Pages(rows), repo, "u", now=restart)
    assert ("u", arrival["listened_at"], arrival["recording_msid"]) in repo.data
    assert repo.state["last_incremental_at"] == restart


def test_resume_original_floor_survives_first_scan_outage():
    started = NOW + timedelta(hours=1)
    bottom = int((started - timedelta(hours=72)).timestamp()) + 1
    rows = [row(T + n, n + 2000) for n in range(1, 1001)] + [row(bottom, 1)]
    first, repo = Pages(rows), MemoryRepository()
    first.fail_on = 3
    with pytest.raises(RuntimeError):
        sync_listens(first, repo, "u", now=started)
    sync_listens(Pages(rows), repo, "u", now=started + timedelta(days=7))
    assert ("u", bottom, str(UUID(int=1))) in repo.data


def test_invalid_snapshot_cannot_clear_reconcile_checkpoint():
    repo = MemoryRepository()
    repo.state["requires_reconcile"] = True
    repo.activate_snapshot = lambda *args, **kwargs: {"deletions_blocked": 1}
    with pytest.raises(ValueError, match="identity"):
        reconcile_history(Pages([row()]), repo, "u", now=NOW)
    assert repo.state["requires_reconcile"]
    assert "last_export_at" not in repo.state
    assert repo.runs[-1]["status"] == "failed"
