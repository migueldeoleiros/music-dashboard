import io
import json
import threading
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.application.reconcile_history import reconcile_history
from music_dashboard.infrastructure.listenbrainz.listens import (
    ExportCorruptError,
    ListenBrainzClient,
)
from music_dashboard.infrastructure.listenbrainz.transport import (
    HttpResponse,
    ListenBrainzTransport,
)
from music_dashboard.infrastructure.persistence.repositories.listening_sync import SyncRepository

pytestmark = pytest.mark.integration

USER = "alice"
T0 = 1_700_000_000
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo(migrated_db, admin_url):
    eng = create_engine(admin_url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO data_generation (id) VALUES (1) "
                "ON CONFLICT (id) DO UPDATE SET ingestion_generation = 0"
            )
        )
    yield SyncRepository(eng)
    eng.dispose()


def export_client(rows, manifest=True):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("listens.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
    ready = {"status": "ready"}
    if manifest:
        ready["listen_count"] = len(rows)
    responses = [
        {"valid": True, "user_name": USER},
        {"payload": {"count": len(rows)}},
        {"export_id": "test"},
        ready,
    ]
    queue = [HttpResponse(200, {}, json.dumps(data).encode()) for data in responses]
    queue.append(HttpResponse(200, {}, archive.getvalue()))

    class HTTP:
        def send(self, request):
            return queue.pop(0)

    return ListenBrainzClient(ListenBrainzTransport(HTTP(), token="synthetic-token"))


def test_mock_http_to_database_manifest_reconcile_deletion_and_duplicate_safety(repo):
    rows = [listen(n) for n in range(10)]
    reconcile_history(export_client(rows), repo, USER, now=NOW)
    assert q(repo, "SELECT count(*) FROM listens") == 10
    reconcile_history(export_client(rows[:-1]), repo, USER, now=NOW + timedelta(days=1))
    assert q(repo, "SELECT count(*) FROM listens") == 9  # exactly 10% allowed
    assert q(repo, "SELECT count(*) FROM raw_listens WHERE tombstoned_at IS NOT NULL") == 1
    # Inflate a truncated artifact with a duplicate: must fail before activation,
    # even if the operator explicitly permits a large deletion.
    truncated = rows[:7] + [rows[0], rows[0]]
    with pytest.raises(ExportCorruptError):
        reconcile_history(
            export_client(truncated),
            repo,
            USER,
            now=NOW + timedelta(days=2),
            allow_large_delete=True,
        )
    assert q(repo, "SELECT count(*) FROM listens") == 9
    assert repo.load_checkpoint(USER)["last_export_at"] == NOW + timedelta(days=1)


def test_no_snapshot_manifest_never_deletes_or_marks_reconcile_complete(repo):
    rows = [listen(n) for n in range(10)]
    reconcile_history(export_client(rows), repo, USER, now=NOW)
    with pytest.raises(ValueError):
        reconcile_history(
            export_client(rows[:1], manifest=False),
            repo,
            USER,
            now=NOW + timedelta(days=1),
            allow_large_delete=True,
        )
    assert q(repo, "SELECT count(*) FROM listens") == 10
    assert repo.load_checkpoint(USER)["last_export_at"] == NOW


def test_manifest_with_missing_identity_retains_quarantine_without_deleting(repo):
    rows = [listen(n) for n in range(10)]
    reconcile_history(export_client(rows), repo, USER, now=NOW)
    with pytest.raises(ValueError):
        reconcile_history(
            export_client([rows[0], {"listened_at": T0}]),
            repo,
            USER,
            now=NOW + timedelta(days=1),
            allow_large_delete=True,
        )
    assert q(repo, "SELECT count(*) FROM listens") == 10
    assert q(repo, "SELECT count(*) FROM quarantined_listens") == 1
    assert repo.load_checkpoint(USER)["last_export_at"] == NOW


def activate(repo, *a, **k):
    k.setdefault("snapshot_complete", True)
    return repo.activate_snapshot(*a, **k)


def msid(n):
    return str(uuid.UUID(int=n + 1))


def listen(n, ts=None, track="Song", artist="Artist", mbid=None, **extra):
    tm = {"track_name": track, "artist_name": artist, "release_name": "Album"}
    if mbid:
        tm["mbid_mapping"] = {
            "recording_mbid": mbid,
            "artists": [{"artist_credit_name": artist, "artist_mbid": mbid}],
        }
    return {
        "listened_at": ts if ts is not None else T0 + n,
        "recording_msid": msid(n),
        "track_metadata": tm,
        **extra,
    }


def q(repo, sql, **p):
    with repo._engine.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def gen(repo):
    return q(repo, "SELECT ingestion_generation FROM data_generation WHERE id = 1")


def test_checkpoint_defaults_and_additive(repo):
    assert repo.load_checkpoint(USER) == {
        "cursor_max_ts": None,
        "last_incremental_at": None,
        "last_export_at": None,
        "ambiguous_boundary_ts": None,
        "requires_reconcile": False,
    }
    repo.save_checkpoint(USER, {"cursor_max_ts": 5, "requires_reconcile": True})
    repo.save_checkpoint(USER, {"last_export_at": NOW})
    repo.save_checkpoint(USER, {"requires_reconcile": False})
    cp = repo.load_checkpoint(USER)
    assert cp["cursor_max_ts"] == 5 and cp["last_export_at"] == NOW
    assert cp["requires_reconcile"] is False
    with pytest.raises(ValueError):
        repo.save_checkpoint(USER, {"bogus": 1})


def test_runs_and_next_retry(repo):
    assert repo.next_retry(USER) is None
    rid = repo.begin_run(USER, "incremental")
    assert repo.next_retry(USER) is None  # unfinished
    retry = NOW + timedelta(minutes=5)
    repo.finish_run(rid, "failed", {"next_retry_at": retry.isoformat()}, "boom")
    assert repo.next_retry(USER) == retry
    assert repo.next_retry(USER, "export") is None
    repo.finish_run(repo.begin_run(USER, "incremental"), "succeeded")
    assert repo.next_retry(USER) is None
    with pytest.raises(ValueError):
        repo.begin_run(USER, "nope")
    with pytest.raises(ValueError):
        repo.finish_run(rid, "running")


def test_upstream_rate_deadline_persists_across_repository_instances_and_job_kinds(repo):
    retry = NOW + timedelta(hours=1)
    run = repo.begin_run(USER, "incremental")
    repo.finish_run(run, "failed", {"rate_limit_until": retry.isoformat()})
    # A newer ordinary job record must not discard an upstream-wide deadline.
    repo.finish_run(repo.begin_run(USER, "reconcile"), "succeeded", {})
    restarted = SyncRepository(repo._engine)
    assert restarted.rate_limit_until(USER) == retry
    assert restarted.rate_limit_until("another-user") is None


def test_lock_exclusive_and_released(repo):
    with repo.locked(USER) as a:
        assert a is True
        with repo.locked(USER) as nested:  # reentrant, same instance/thread
            assert nested is True
        other_repo = SyncRepository(repo._engine)
        with other_repo.locked(USER) as b:
            assert b is False
            with repo.locked("bob") as other:
                assert other is True
        # holder survives transactions on other connections
        repo.save_checkpoint(USER, {"cursor_max_ts": 1})
    with repo.locked(USER) as again:
        assert again is True


def test_lock_across_threads(repo):
    got = []
    with repo.locked(USER):

        def other():
            with repo.locked(USER) as ok:
                got.append(ok)

        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert got == [False]


def test_ingest_idempotent_and_generation(repo):
    batch = [listen(1, mbid=str(uuid.uuid4())), listen(2), listen(3, ts=T0 + 1)]  # same second
    assert repo.ingest(USER, batch, "api", NOW) == 3
    g = gen(repo)
    assert g == 1
    assert q(repo, "SELECT count(*) FROM listens") == 3
    assert q(repo, "SELECT count(*) FROM raw_listens") == 3
    assert q(repo, "SELECT count(*) FROM listen_artists") == 3
    assert repo.ingest(USER, batch, "api", NOW + timedelta(hours=1)) == 0
    assert gen(repo) == g  # no data change
    assert q(repo, "SELECT max(fetched_at) FROM raw_listens") == NOW + timedelta(hours=1)


def test_ingest_remap_updates_normalized_not_identity(repo):
    repo.ingest(USER, [listen(1, track="Old")], "api", NOW)
    g = gen(repo)
    new_mbid = str(uuid.uuid4())
    assert repo.ingest(USER, [listen(1, track="New", artist="A2", mbid=new_mbid)], "api", NOW) == 1
    assert gen(repo) == g + 1
    assert q(repo, "SELECT track_name FROM listens") == "New"
    assert str(q(repo, "SELECT recording_mbid FROM listens")) == new_mbid
    assert q(repo, "SELECT count(*) FROM listens") == 1
    assert q(repo, "SELECT artist_name FROM listen_artists") == "A2"
    assert str(q(repo, "SELECT artist_mbid FROM listen_artists")) == new_mbid
    assert q(repo, "SELECT payload->'track_metadata'->>'track_name' FROM raw_listens") == "New"


def test_multi_artist_positions(repo):
    p = listen(1)
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    p["track_metadata"]["artist_name"] = "A & B"
    p["track_metadata"]["mbid_mapping"] = {
        "artists": [
            {"artist_credit_name": "A", "artist_mbid": ids[0]},
            {"artist_credit_name": "B", "artist_mbid": ids[1]},
        ]
    }
    repo.ingest(USER, [p], "api", NOW)
    with repo._engine.connect() as c:
        rows = c.execute(
            text("SELECT position, artist_name FROM listen_artists ORDER BY position")
        ).all()
    assert rows == [(0, "A"), (1, "B")]


def test_quarantine_invalid_identity(repo):
    bad = [
        {"track_metadata": {}},  # nothing
        {"listened_at": T0, "recording_msid": "not-a-uuid"},
        {"listened_at": "x", "recording_msid": msid(1)},
        {"listened_at": T0, "recording_msid": str(uuid.UUID(int=0))},
        listen(9, user_name="mallory"),
        "garbage",
    ]
    assert repo.ingest(USER, [*bad, listen(1)], "api", NOW) == 1
    assert q(repo, "SELECT count(*) FROM quarantined_listens") == len(bad)
    assert q(repo, "SELECT count(*) FROM listens") == 1
    repo.ingest(USER, bad, "api", NOW)  # no duplicate quarantine
    assert q(repo, "SELECT count(*) FROM quarantined_listens") == len(bad)
    # msid fallback from additional_info
    p = listen(2)
    del p["recording_msid"]
    p["track_metadata"]["additional_info"] = {"recording_msid": msid(2)}
    assert repo.ingest(USER, [p], "api", NOW) == 1


def seed_history(repo, n=20):
    repo.ingest(USER, [listen(i) for i in range(n)], "export", NOW)


def snap_rows(ids):
    return [listen(i) for i in ids]


def test_snapshot_tombstones_scope_and_preserves_new(repo):
    seed_history(repo, 20)
    snap_at = NOW + timedelta(days=1)
    # arrives after snapshot started and has listened_at beyond snapshot
    repo.ingest(USER, [listen(50, ts=int(snap_at.timestamp()) + 100)], "api", snap_at)
    # arrives during snapshot: listened_at in scope but fetched_at after
    repo.ingest(USER, [listen(51, ts=T0 + 5000)], "api", snap_at + timedelta(seconds=1))
    g = gen(repo)
    res = activate(repo, USER, snap_rows(range(19)), snap_at, allow_large_delete=False)
    assert res["tombstoned"] == 1 and res["deleted_listens"] == 1
    assert res["changed"] == 0 and res["deletions_blocked"] == 0
    assert gen(repo) == g + 1
    assert q(repo, "SELECT count(*) FROM raw_listens WHERE tombstoned_at IS NOT NULL") == 1
    assert q(repo, "SELECT count(*) FROM listens") == 21  # 19 + 2 preserved
    assert q(repo, "SELECT count(*) FROM listens WHERE recording_msid = :m", m=msid(19)) == 0
    assert q(repo, "SELECT count(*) FROM listen_artists") == 21
    # reappearing revives raw and normalized
    res = activate(repo, USER, snap_rows(range(20)), snap_at)
    assert res["revived"] == 1 and res["changed"] == 1
    assert q(repo, "SELECT count(*) FROM listens") == 22
    assert q(repo, "SELECT count(*) FROM raw_listens WHERE tombstoned_at IS NOT NULL") == 0


def test_snapshot_large_delete_guard_and_override(repo):
    seed_history(repo, 20)
    g = gen(repo)
    with pytest.raises(ValueError, match="override") as e:
        activate(repo, USER, snap_rows(range(10)) + [listen(99)], NOW + timedelta(days=1))
    assert "Song" not in str(e.value)
    # rolled back entirely: new row 99 not applied
    assert q(repo, "SELECT count(*) FROM listens") == 20 and gen(repo) == g
    res = activate(
        repo, USER, snap_rows(range(10)), NOW + timedelta(days=1), allow_large_delete=True
    )
    assert res["tombstoned"] == 10
    assert q(repo, "SELECT count(*) FROM listens") == 10


def test_snapshot_empty_or_invalid_blocks_deletes(repo):
    seed_history(repo, 20)
    snap_at = NOW + timedelta(days=1)
    res = activate(repo, USER, [], snap_at, allow_large_delete=True)
    assert res["deletions_blocked"] == 1 and res["tombstoned"] == 0
    rows = [*snap_rows(range(5)), {"listened_at": T0, "recording_msid": "bad"}]
    res = activate(repo, USER, rows, snap_at, allow_large_delete=True)
    assert res["deletions_blocked"] == 1 and res["invalid"] == 1 and res["quarantined"] == 1
    assert q(repo, "SELECT count(*) FROM listens") == 20
    assert q(repo, "SELECT count(*) FROM raw_listens WHERE tombstoned_at IS NOT NULL") == 0
    assert q(repo, "SELECT count(*) FROM quarantined_listens") == 1


def test_snapshot_parse_error_mutates_nothing(repo):
    seed_history(repo, 20)
    g = gen(repo)

    def rows():
        yield listen(100)
        raise RuntimeError("stream broke")

    with pytest.raises(RuntimeError):
        activate(repo, USER, rows(), NOW + timedelta(days=1), allow_large_delete=True)
    with pytest.raises(ValueError):
        activate(
            repo, USER, [listen(100), {"listened_at": T0, "x": {1, 2}}], NOW + timedelta(days=1)
        )
    assert q(repo, "SELECT count(*) FROM listens") == 20 and gen(repo) == g
    assert q(repo, "SELECT count(*) FROM raw_listens") == 20


def test_snapshot_upserts_remaps_and_new_rows(repo):
    seed_history(repo, 20)
    rows = snap_rows(range(20))
    rows[3] = listen(3, track="Remapped")
    rows.append(listen(77))
    res = activate(repo, USER, rows, NOW + timedelta(days=1))
    assert res["changed"] == 2 and res["inserted"] == 1 and res["updated"] == 1
    assert q(repo, "SELECT track_name FROM listens WHERE recording_msid = :m", m=msid(3)) == (
        "Remapped"
    )
    assert q(repo, "SELECT count(*) FROM listens") == 21


def test_lock_reentrant_keeps_lock_until_outermost_exit(repo):
    other = SyncRepository(repo._engine)
    with repo.locked(USER) as a:
        with repo.locked(USER) as b:
            assert a and b
        with other.locked(USER) as still:
            assert still is False  # inner exit did not release
    with other.locked(USER) as free:
        assert free is True


def test_top_level_mbid_mapping_remap_preserves_identity(repo):
    repo.ingest(USER, [listen(1)], "api", NOW)
    rec, rel, rg, a1, a2 = (str(uuid.uuid4()) for _ in range(5))
    p = listen(1)
    p["track_metadata"]["artist_name"] = "A & B"
    p["mbid_mapping"] = {
        "recording_mbid": rec,
        "release_mbid": rel,
        "release_group_mbid": rg,
        "artists": [
            {"artist_credit_name": "A", "artist_mbid": a1},
            {"artist_credit_name": "B", "artist_mbid": a2},
        ],
    }
    assert repo.ingest(USER, [p], "api", NOW) == 1
    assert q(repo, "SELECT count(*) FROM listens") == 1
    assert q(repo, "SELECT count(*) FROM raw_listens") == 1
    assert q(repo, "SELECT recording_msid FROM listens") == uuid.UUID(msid(1))
    assert str(q(repo, "SELECT recording_mbid FROM listens")) == rec
    assert str(q(repo, "SELECT release_mbid FROM listens")) == rel
    assert str(q(repo, "SELECT release_group_mbid FROM listens")) == rg
    with repo._engine.connect() as c:
        rows = c.execute(
            text("SELECT position, artist_name, artist_mbid FROM listen_artists ORDER BY 1")
        ).all()
    assert [(r[0], r[1], str(r[2])) for r in rows] == [(0, "A", a1), (1, "B", a2)]


def test_epoch_zero_is_valid_identity(repo):
    assert repo.ingest(USER, [listen(1, ts=0)], "api", NOW) == 1
    assert q(repo, "SELECT count(*) FROM quarantined_listens") == 0
    assert q(repo, "SELECT count(*) FROM listens WHERE listened_at = to_timestamp(0)") == 1


def test_guard_denominator_ignores_newer_arrivals(repo):
    seed_history(repo, 20)
    snap_at = NOW + timedelta(days=1)
    late = snap_at + timedelta(seconds=1)
    # 100 listens fetched after the snapshot started must not dilute the 10% guard
    repo.ingest(USER, [listen(200 + i) for i in range(100)], "api", late)
    with pytest.raises(ValueError, match="override"):
        activate(repo, USER, snap_rows(range(10)) + [listen(200 + i) for i in range(100)], snap_at)
    assert q(repo, "SELECT count(*) FROM listens") == 120


def test_next_retry_uses_stats_next_retry_at(repo):
    rid = repo.begin_run(USER, "export")
    retry = NOW + timedelta(minutes=30)
    repo.finish_run(rid, "failed", {"next_retry_at": retry.isoformat(), "attempts": 2})
    assert repo.next_retry(USER) == retry
    assert repo.next_retry(USER, "export") == retry


def rich(n, **kw):
    p = listen(n, **kw)
    p["mbid_mapping"] = {
        "recording_mbid": RICH["rec"],
        "artists": [{"artist_credit_name": "Artist", "artist_mbid": RICH["art"]}],
    }
    return p


RICH = {"rec": str(uuid.uuid4()), "art": str(uuid.uuid4())}


def test_sparse_api_never_erases_export_enrichment(repo):
    snap_at = NOW + timedelta(days=1)
    activate(repo, USER, [rich(1)], snap_at)
    g = gen(repo)
    # sparse API payloads: omitted, null and empty mapping must not erase anything
    for sparse in (
        listen(1),
        {**listen(1), "mbid_mapping": None},
        {**listen(1), "mbid_mapping": {}},
    ):
        assert repo.ingest(USER, [sparse], "api", snap_at + timedelta(hours=1)) == 0
    assert gen(repo) == g
    assert str(q(repo, "SELECT recording_mbid FROM listens")) == RICH["rec"]
    assert str(q(repo, "SELECT artist_mbid FROM listen_artists")) == RICH["art"]
    assert q(repo, "SELECT payload ? 'mbid_mapping' FROM raw_listens") is True
    # a non-empty API remap still wins
    new = str(uuid.uuid4())
    assert (
        repo.ingest(USER, [{**listen(1), "mbid_mapping": {"recording_mbid": new}}], "api", NOW) == 1
    )
    assert str(q(repo, "SELECT recording_mbid FROM listens")) == new
    assert str(q(repo, "SELECT artist_mbid FROM listen_artists")) == RICH["art"]
    # export is authoritative: remap, then clear; identity stable
    other = str(uuid.uuid4())
    remap = rich(1)
    remap["mbid_mapping"]["recording_mbid"] = other
    res = activate(repo, USER, [remap], snap_at)
    assert res["changed"] == 1
    assert str(q(repo, "SELECT recording_mbid FROM listens")) == other
    g = gen(repo)
    assert activate(repo, USER, [remap], snap_at)["changed"] == 0 and gen(repo) == g
    res = activate(repo, USER, [listen(1)], snap_at)
    assert res["changed"] == 1
    assert q(repo, "SELECT recording_mbid FROM listens") is None
    assert q(repo, "SELECT artist_mbid FROM listen_artists") is None
    assert q(repo, "SELECT count(*) FROM listens") == 1
    assert q(repo, "SELECT count(*) FROM raw_listens") == 1


def test_no_completeness_evidence_blocks_deletes(repo):
    seed_history(repo, 20)
    res = repo.activate_snapshot(USER, snap_rows(range(19)), NOW + timedelta(days=1))
    assert res["deletions_blocked"] == 1 and res["tombstoned"] == 0
    res = repo.activate_snapshot(
        USER, snap_rows(range(2)), NOW + timedelta(days=1), allow_large_delete=True
    )
    assert res["deletions_blocked"] == 1
    assert q(repo, "SELECT count(*) FROM listens") == 20
    assert q(repo, "SELECT count(*) FROM raw_listens WHERE tombstoned_at IS NOT NULL") == 0


def test_run_stats_and_scan_state(repo):
    assert repo.load_scan(USER) is None
    scan = {"upper": 100, "lower": 10, "next_max_ts": 90}
    rid = repo.begin_run(USER, "incremental", {"scan": scan})
    assert repo.load_scan(USER) == scan
    repo.update_run_stats(rid, {"scan": {**scan, "next_max_ts": 50}})
    assert repo.load_scan(USER)["next_max_ts"] == 50
    repo.finish_run(rid, "failed", {"next_retry_at": NOW.isoformat()}, "boom")
    assert repo.load_scan(USER)["next_max_ts"] == 50  # merged, scan preserved
    assert repo.next_retry(USER) == NOW
    repo.finish_run(repo.begin_run(USER, "incremental"), "succeeded")
    assert repo.load_scan(USER) is None
    stale = repo.begin_run(USER, "incremental", {"scan": scan})
    assert repo.load_scan(USER) == scan
    fresh = repo.begin_run(USER, "incremental")  # restart: stale run aborted
    assert q(repo, "SELECT status FROM sync_runs WHERE id = :i", i=stale) == "aborted"
    assert q(repo, "SELECT finished_at IS NOT NULL FROM sync_runs WHERE id = :i", i=stale)
    assert repo.load_scan(USER) is None
    repo.update_run_stats(fresh, {"scan": scan})
    repo.finish_run(fresh, "ambiguous")
    assert repo.load_scan(USER) is None  # only running/failed
    other = repo.begin_run(USER, "export", {"scan": scan})
    assert repo.load_scan(USER) is None  # incremental only
    repo.finish_run(other, "failed")
    with pytest.raises(ValueError):
        repo.update_run_stats(10**9, {})
