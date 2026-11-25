"""Real-PostgreSQL proof: definer admission, coalescing, expiry, grants, worker recovery."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from music_dashboard.infrastructure.persistence.repositories.refresh import RefreshRepository

pytestmark = pytest.mark.integration
EXPIRE_ALL = (
    "UPDATE refresh_state SET expires_at = now() - interval '1 s', "
    "cooldown_until = now() - interval '1 s'"
)
PRIV = "SELECT has_function_privilege('public', 'public.public_refresh_request()', 'EXECUTE')"
ADMIT = "SELECT * FROM public.public_refresh_request()"


@pytest.fixture
def api(migrated_db, api_url):
    eng = create_engine(api_url)
    yield eng
    eng.dispose()


def admit(api):
    with api.connect() as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ WRITE")
        return c.execute(text(ADMIT)).mappings().one()


def status(api):
    with api.connect() as c:
        return c.execute(text("SELECT * FROM public.public_refresh_status()")).mappings().one()


def test_api_has_no_table_grants_but_can_execute_functions(api, migrated_db):
    assert status(api)["status"] == "idle"
    with pytest.raises(DBAPIError, match="permission denied"):
        with api.connect() as c:
            c.execute(text("SELECT * FROM refresh_state"))
    with migrated_db.connect() as c:
        assert not c.execute(text(PRIV)).scalar_one()
        cfg = c.execute(
            text("SELECT prosecdef, proconfig FROM pg_proc WHERE proname='public_refresh_request'")
        ).one()
        assert cfg[0] and any("search_path=pg_catalog" in x for x in cfg[1])


def test_writer_can_execute_admission_and_downgrade_restores_300s(migrated_db, writer, admin_url):
    from alembic import command

    from tests.integration.conftest import alembic_cfg

    with writer.connect() as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ WRITE")
        row = c.execute(text(ADMIT)).mappings().one()
    assert row["status"] == "pending"
    window = row["cooldown_until"] - row["server_now"]
    assert timedelta(seconds=599) < window <= timedelta(seconds=600)
    command.downgrade(alembic_cfg(admin_url), "0007")
    with migrated_db.connect() as c:
        assert not c.execute(
            text(
                "SELECT has_function_privilege("
                "'music_writer', 'public.public_refresh_request()', 'EXECUTE')"
            )
        ).scalar_one()
        assert (
            "300"
            in c.execute(
                text("SELECT prosrc FROM pg_proc WHERE proname='public_refresh_request'")
            ).scalar_one()
        )


def test_admission_needs_read_write_and_default_is_read_only(api):
    with pytest.raises(DBAPIError):
        with api.begin() as c:  # role default is read-only
            c.execute(text(ADMIT))


def test_new_request_coalesces_without_extending_cooldown(api):
    first = admit(api)
    assert first["status"] == "pending" and first["coalesced"] is False
    second = admit(api)
    assert second["request_id"] == first["request_id"] and second["coalesced"] is True
    assert second["cooldown_until"] == first["cooldown_until"]
    assert status(api)["request_id"] == first["request_id"]


def test_cooldown_blocks_new_request_after_finish(api, migrated_db):
    first = admit(api)
    repo = RefreshRepository(migrated_db)
    assert repo.finish(first["request_id"], "succeeded")
    again = admit(api)
    assert again["request_id"] == first["request_id"] and again["status"] == "succeeded"
    with migrated_db.begin() as c:
        c.execute(text("UPDATE refresh_state SET cooldown_until = now() - interval '1 s'"))
    fresh = admit(api)
    assert fresh["request_id"] != first["request_id"] and fresh["status"] == "pending"


def test_expiry_virtual_on_read_and_persisted_on_admission(api, migrated_db):
    first = admit(api)
    with migrated_db.begin() as c:
        c.execute(text(EXPIRE_ALL))
    assert status(api)["status"] == "expired"
    nxt = admit(api)
    assert nxt["request_id"] != first["request_id"] and nxt["status"] == "pending"


def test_worker_lifecycle_and_orphan_recovery(api, migrated_db):
    req = admit(api)
    repo = RefreshRepository(migrated_db)
    assert repo.has_active()
    claimed = repo.claim()
    assert claimed and claimed.request_id == req["request_id"]
    assert repo.mark_running(req["request_id"])
    assert status(api)["status"] == "running"
    # caller holds the sync lock, so a persisted 'running' row is an orphan of a dead worker
    again = repo.claim()
    assert again and status(api)["status"] == "pending"
    assert status(api)["detail"] == "worker_restarted"
    assert repo.mark_deferred(req["request_id"], "requires_reconcile")
    assert status(api)["status"] == "deferred"
    assert repo.finish(
        req["request_id"], "failed", "sync_failed", datetime.now(UTC) + timedelta(hours=1)
    )
    assert not repo.mark_running(req["request_id"])  # terminal: cannot be resurrected
    assert not repo.mark_running(uuid.uuid4())
    assert not repo.has_active()


def test_claim_expires_stale(api, migrated_db):
    admit(api)
    with migrated_db.begin() as c:
        c.execute(text("UPDATE refresh_state SET expires_at = now() - interval '1 s'"))
    assert RefreshRepository(migrated_db).claim() is None
    assert status(api)["status"] == "expired"


def test_admission_takes_no_caller_policy_and_cooldown_is_fixed(api):
    first = admit(api)
    window = first["cooldown_until"] - first["server_now"]
    assert timedelta(seconds=599) < window <= timedelta(seconds=600)
    with pytest.raises(DBAPIError):
        with api.connect() as c, c.begin():
            c.exec_driver_sql("SET TRANSACTION READ WRITE")
            c.execute(text("SELECT * FROM public.public_refresh_request(1, 1)"))


def test_stale_worker_cannot_overwrite_newer_request(api, migrated_db):
    old = admit(api)
    with migrated_db.begin() as c:
        c.execute(text(EXPIRE_ALL))
    new = admit(api)
    repo = RefreshRepository(migrated_db)
    assert new["request_id"] != old["request_id"]
    assert not repo.finish(old["request_id"], "succeeded")
    assert not repo.mark_deferred(old["request_id"], "rate_limited")
    assert status(api)["request_id"] == new["request_id"] and status(api)["status"] == "pending"


# -- concurrency, real advisory lock, real writer role -------------------------------------

USER = "refresh-user"


class EmptyPages:
    def __init__(self):
        self.calls = 0

    def page(self, user, **kwargs):
        self.calls += 1
        return []


@pytest.fixture
def writer_url():
    import os

    from music_dashboard.infrastructure.persistence.database import resolve_url

    url = os.environ.get("TEST_WRITER_DATABASE_URL")
    if not url:
        pytest.skip("TEST_WRITER_DATABASE_URL not set")
    return resolve_url(url)


@pytest.fixture
def writer(migrated_db, writer_url):
    eng = create_engine(writer_url)
    with eng.connect() as c:
        assert c.execute(text("SELECT current_user")).scalar_one() == "music_writer"
    yield eng
    eng.dispose()


@pytest.fixture
def worker(monkeypatch, writer, writer_url):
    """Run the real job against the real DB as music_writer; upstream is a fake client."""
    from music_dashboard.application.jobs import sync as job

    monkeypatch.setenv("MUSIC_DATABASE_URL", writer_url)
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.setenv("LISTENBRAINZ_USERNAME", USER)
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", "synthetic-test-token")
    client = EmptyPages()
    monkeypatch.setattr(job, "_build_client", lambda config: client)
    return job, client


def seed_checkpoint(writer, **values):
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )

    SyncRepository(writer).save_checkpoint(USER, values)


def test_concurrent_admissions_share_one_request(api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    n = 8
    barrier = Barrier(n)

    def one(_):
        barrier.wait()
        return admit(api)

    with ThreadPoolExecutor(max_workers=n) as pool:
        rows = list(pool.map(one, range(n)))
    assert len({r["request_id"] for r in rows}) == 1
    assert sum(1 for r in rows if not r["coalesced"]) == 1
    assert len({r["cooldown_until"] for r in rows}) == 1


def test_sync_lock_is_exclusive_across_repository_instances(writer):
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )

    first, second = SyncRepository(writer), SyncRepository(writer)
    with first.locked(USER) as a:
        assert a
        with second.locked(USER) as b:
            assert not b
    with second.locked(USER) as c:
        assert c


def test_success_after_initial_export_completes_correlated_request(api, writer, worker):
    job, client = worker
    now = datetime.now(UTC)
    seed_checkpoint(writer, last_export_at=now, last_incremental_at=now)
    req = admit(api)
    assert job.run_public_refresh()["status"] == "succeeded"
    done = status(api)
    assert done["request_id"] == req["request_id"] and done["status"] == "succeeded"
    assert done["started_at"] is not None and done["finished_at"] is not None
    assert client.calls >= 1
    with writer.connect() as c:
        kinds = c.execute(text("SELECT kind FROM sync_runs")).scalars().all()
    assert kinds == ["incremental"]  # never an export
    assert job.run_public_refresh() == {"status": "idle"}


def test_missing_initial_export_defers_without_upstream(api, writer, worker):
    job, client = worker
    admit(api)
    assert job.run_public_refresh() == {"status": "deferred"}
    got = status(api)
    assert got["status"] == "deferred" and got["detail"] == "awaiting_initial_export"
    assert client.calls == 0
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    assert job.run_public_refresh()["status"] == "succeeded"  # deferred is retried, same request


def test_requires_reconcile_defers_then_completes_once_cleared(api, writer, worker):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC), requires_reconcile=True)
    req = admit(api)
    assert job.run_public_refresh() == {"status": "deferred"}
    got = status(api)
    assert got["detail"] == "requires_reconcile" and got["request_id"] == req["request_id"]
    assert client.calls == 0
    seed_checkpoint(writer, requires_reconcile=False)
    assert job.run_public_refresh()["status"] == "succeeded"
    assert status(api)["request_id"] == req["request_id"]


def test_busy_lock_leaves_request_then_recovers(api, writer, worker):
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )

    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    req = admit(api)
    with SyncRepository(writer).locked(USER) as held:
        assert held
        assert job.run_public_refresh() == {"status": "busy"}
        assert client.calls == 0
        assert status(api)["status"] == "pending"
    assert job.run_public_refresh()["status"] == "succeeded"
    assert status(api)["request_id"] == req["request_id"]


def test_crash_while_running_is_recovered_by_next_worker(api, writer, worker):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    req = admit(api)
    repo = RefreshRepository(writer)
    assert repo.mark_running(req["request_id"])  # worker died here; its lock is gone
    assert status(api)["status"] == "running"
    assert job.run_public_refresh()["status"] == "succeeded"
    done = status(api)
    assert done["request_id"] == req["request_id"] and done["status"] == "succeeded"
    assert client.calls >= 1


def test_expired_request_is_not_executed(api, writer, worker):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    admit(api)
    with writer.begin() as c:
        c.execute(text("UPDATE refresh_state SET expires_at = now() - interval '1 s'"))
    assert job.run_public_refresh() == {"status": "idle"}
    assert client.calls == 0
    assert status(api)["status"] == "expired"


def test_read_write_is_per_transaction_and_tables_stay_denied(api):
    admit(api)
    with api.connect() as c:  # fresh transaction on the same engine: read-only again
        assert c.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
    for stmt in (
        "INSERT INTO refresh_state (singleton) VALUES (false)",
        "UPDATE refresh_state SET status = 'succeeded'",
        "DELETE FROM refresh_state",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            with api.connect() as c, c.begin():
                c.exec_driver_sql("SET TRANSACTION READ WRITE")
                c.execute(text(stmt))
    assert status(api)["status"] == "pending"


def test_execute_revoked_from_public_on_both_functions(migrated_db):
    with migrated_db.connect() as c:
        for sig in ("public_refresh_request()", "public_refresh_status()"):
            sql = f"SELECT has_function_privilege('public', 'public.{sig}', 'EXECUTE')"
            assert not c.execute(text(sql)).scalar_one(), sig
            api_sql = f"SELECT has_function_privilege('music_api', 'public.{sig}', 'EXECUTE')"
            assert c.execute(text(api_sql)).scalar_one(), sig


def test_worker_executes_request_regardless_of_legacy_flag(api, writer, worker, monkeypatch):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    req = admit(api)
    monkeypatch.setenv("MUSIC_PUBLIC_REFRESH_ENABLED", "false")  # legacy flag is ignored
    assert job.run_public_refresh()["status"] == "succeeded"
    got = status(api)
    assert got["request_id"] == req["request_id"] and got["status"] == "succeeded"


def test_expiry_crossed_between_claim_and_start_never_calls_upstream(
    api, writer, worker, monkeypatch
):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    req = admit(api)
    original = RefreshRepository.mark_running

    def cross_deadline(self, rid):
        # Simulate crossing expiry after claim, without sleeps or process clock skew.
        with writer.begin() as c:
            c.execute(
                text("UPDATE refresh_state SET expires_at = clock_timestamp() - interval '1 s'")
            )
        return original(self, rid)

    monkeypatch.setattr(RefreshRepository, "mark_running", cross_deadline)
    assert job.run_public_refresh(now=datetime.now(UTC) - timedelta(hours=1)) == {
        "status": "superseded"
    }
    assert client.calls == 0
    got = status(api)
    assert got["request_id"] == req["request_id"] and got["status"] == "expired"


@pytest.mark.parametrize("transition", ["running", "succeeded"])
def test_transition_rechecks_expiry_after_waiting_for_unchanged_row_lock(api, writer, transition):
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    req = admit(api)
    with writer.begin() as c:
        c.execute(text("UPDATE refresh_state SET expires_at = clock_timestamp() + interval '2 s'"))
    repo = RefreshRepository(writer)
    with ThreadPoolExecutor(max_workers=1) as pool, writer.connect() as holder, holder.begin():
        holder.execute(text("SELECT singleton FROM refresh_state FOR UPDATE"))
        action = (
            repo.mark_running
            if transition == "running"
            else lambda rid: repo.finish(rid, transition)
        )
        future = pool.submit(action, req["request_id"])
        # Confirm the second transaction really waits on our unchanged row. This is
        # database synchronization, not an arbitrary delay to guess thread scheduling.
        deadline = monotonic() + 5
        while True:
            holder.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = holder.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE usename = current_user AND pid <> pg_backend_pid() "
                    "AND wait_event_type = 'Lock' AND query LIKE '%refresh_state%')"
                )
            ).scalar_one()
            if waiting:
                break
            assert monotonic() < deadline, "transition never waited for the row lock"
        # Real PostgreSQL clock semantics require crossing a real deadline; derive
        # the wait from that deadline rather than sleep a guessed number of seconds.
        holder.execute(
            text(
                "SELECT pg_sleep(GREATEST(0, "
                "EXTRACT(epoch FROM (expires_at - clock_timestamp()))) + 0.01) "
                "FROM refresh_state"
            )
        )
        holder.commit()  # no row changes: old UPDATE predicates would not be rechecked
        assert future.result(timeout=5) is False
    got = status(api)
    assert got["status"] == "expired" and got["started_at"] is None


def test_claim_uses_database_clock_not_old_scan_anchor(api, writer, worker):
    job, client = worker
    seed_checkpoint(writer, last_export_at=datetime.now(UTC))
    admit(api)
    with writer.begin() as c:
        c.execute(text("UPDATE refresh_state SET expires_at = clock_timestamp() - interval '1 s'"))
    assert job.run_public_refresh(now=datetime.now(UTC) - timedelta(hours=1)) == {"status": "idle"}
    assert client.calls == 0
    assert status(api)["status"] == "expired"


@pytest.mark.parametrize("transition", ["running", "deferred", "succeeded", "failed"])
def test_expiry_is_terminal_even_when_stale_worker_finishes(api, writer, transition):
    req = admit(api)
    repo = RefreshRepository(writer)
    assert repo.mark_running(req["request_id"])
    with writer.begin() as c:
        c.execute(text("UPDATE refresh_state SET expires_at = now() - interval '1 s'"))
    assert status(api)["status"] == "expired"
    if transition == "running":
        result = repo.mark_running(req["request_id"])
    elif transition == "deferred":
        result = repo.mark_deferred(req["request_id"], "requires_reconcile")
    else:
        result = repo.finish(req["request_id"], transition)
    assert result is False
    assert status(api)["status"] == "expired"
    with writer.connect() as c:
        assert c.execute(text("SELECT status FROM refresh_state")).scalar_one() == "expired"


def test_repository_admit_via_writer_respects_cooldown_and_coalesces(migrated_db, writer):
    with migrated_db.begin() as c:
        c.execute(text(EXPIRE_ALL))
    repo = RefreshRepository(writer)
    assert repo.admit() == ("pending", False)
    assert repo.admit() == ("pending", True)  # coalesced onto the in-flight request
    with migrated_db.begin() as c:
        c.execute(text("UPDATE refresh_state SET status='succeeded', finished_at=now()"))
    assert repo.admit()[0] == "succeeded"  # cooldown active: nothing new admitted
