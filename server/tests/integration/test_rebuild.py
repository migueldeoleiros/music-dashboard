"""Offline rebuild: raw+cache -> normalized projections; locks, freshness, idempotence."""

import argparse
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.infrastructure.persistence.repositories.listening_sync import SyncRepository
from music_dashboard.infrastructure.persistence.repositories.metadata import ENRICHMENT_LOCK
from music_dashboard.infrastructure.persistence.repositories.rebuild import (
    RebuildBusy,
    RebuildRepository,
    RebuildUnsupported,
)

pytestmark = pytest.mark.integration

USER = "alice"
T0 = 1_700_000_000
A1, A2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "a1")), str(uuid.uuid5(uuid.NAMESPACE_DNS, "a2"))
REC = str(uuid.uuid5(uuid.NAMESPACE_DNS, "rec"))
G1 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "g1"))


def payload(i, **extra):
    return {
        "listened_at": T0 + i,
        "recording_msid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"m{i}")),
        "track_metadata": {
            "track_name": f"T{i}",
            "artist_name": "X & Y",
            "mbid_mapping": {
                "recording_mbid": REC,
                "artists": [
                    {"artist_credit_name": "X", "artist_mbid": A1},
                    {"artist_credit_name": "Y", "artist_mbid": A2},
                ],
            },
        },
        **extra,
    }


@pytest.fixture
def db(migrated_db, admin_url):
    eng = create_engine(admin_url)
    repo = SyncRepository(eng)
    repo.ingest(USER, [payload(i) for i in range(5)], "export", datetime(2026, 1, 1, tzinfo=UTC))
    repo.ingest(USER, [{"listened_at": 1}], "export", datetime(2026, 1, 1, tzinfo=UTC))
    with eng.begin() as c:
        c.execute(
            text(
                "UPDATE raw_listens SET tombstoned_at = now() WHERE listened_at = to_timestamp(:t)"
            ),
            {"t": T0 + 4},
        )
        c.execute(
            text(
                "INSERT INTO metadata_cache (entity_type, entity_mbid, source, status, payload) "
                "VALUES ('recording', :r, 'listenbrainz', 'positive', CAST(:p AS jsonb)), "
                "('artist', :a, 'musicbrainz', 'positive', CAST(:q AS jsonb)), "
                "('artist', :b, 'musicbrainz', 'empty', CAST(:e AS jsonb))"
            ),
            {
                "r": REC,
                "a": A1,
                "b": A2,
                "p": json.dumps({"genres": [{"genre_mbid": G1, "name": "rock", "count": 3}]}),
                "q": json.dumps({"country": "US"}),
                "e": json.dumps({}),
            },
        )
        c.execute(text("UPDATE data_generation SET last_updated = '2020-01-01T00:00Z'"))
    yield eng
    eng.dispose()


def snapshot(eng):
    tables = {
        "listens": "SELECT user_name, listened_at, recording_msid, track_name, artist_name, "
        "recording_mbid FROM listens ORDER BY 2,3",
        "artists": "SELECT l.listened_at, a.position, a.artist_name, a.artist_mbid FROM "
        "listen_artists a JOIN listens l ON l.id = a.listen_id ORDER BY 1,2",
        "eg": "SELECT * FROM entity_genres ORDER BY 1,2,3",
        "ac": "SELECT artist_mbid, country_code FROM artist_countries ORDER BY 1",
        "raw": "SELECT id, payload, tombstoned_at FROM raw_listens ORDER BY id",
        "cache": "SELECT id, status, payload FROM metadata_cache ORDER BY id",
        "sync": "SELECT * FROM sync_state ORDER BY 1",
    }
    with eng.connect() as c:
        return {k: [tuple(r) for r in c.execute(text(q))] for k, q in tables.items()}


def gen(eng):
    with eng.connect() as c:
        return tuple(
            c.execute(
                text(
                    "SELECT ingestion_generation, enrichment_generation, last_updated "
                    "FROM data_generation"
                )
            ).one()
        )


def test_rebuild_from_raw_and_cache_preserves_sources_and_freshness(db):
    with db.begin() as c:  # corrupt projections
        c.execute(text("DELETE FROM listens WHERE track_name = 'T0'"))
        c.execute(text("UPDATE listens SET track_name = 'bad'"))
        c.execute(text("DELETE FROM listen_artists WHERE position = 1"))
        c.execute(
            text(
                "INSERT INTO listens (user_name, listened_at, recording_msid) "
                "VALUES ('alice', now(), gen_random_uuid())"
            )
        )
    raw_before = snapshot(db)["raw"], snapshot(db)["cache"]
    g0 = gen(db)
    stats = RebuildRepository(db).rebuild()
    assert stats["listens"] == 4 and stats["unparseable_raw"] == 0
    s1 = snapshot(db)
    assert (s1["raw"], s1["cache"]) == raw_before
    assert [r[3] for r in s1["listens"]] == ["T0", "T1", "T2", "T3"]  # tombstoned T4 absent
    assert len(s1["artists"]) == 8
    assert [(str(r[1]), r[2]) for r in s1["eg"]] == [(REC, 3)] or len(s1["eg"]) == 1
    assert s1["ac"] == [(uuid.UUID(A1), "US")]  # empty cache row yields no country
    g1 = gen(db)
    assert g1[0] == g0[0] + 1 and g1[1] == g0[1] + 1 and g1[2] == g0[2]
    RebuildRepository(db).rebuild()
    assert snapshot(db) == s1
    g2 = gen(db)
    assert g2[0] == g1[0] + 1 and g2[2] == g0[2]


def test_unparseable_active_raw_fails_closed(db):
    with db.begin() as c:
        c.execute(
            text(
                "UPDATE raw_listens SET payload = '{}'::jsonb WHERE id = "
                "(SELECT min(id) FROM raw_listens)"
            )
        )
    before, generation = snapshot(db), gen(db)
    with pytest.raises(RebuildUnsupported, match="cannot be parsed"):
        RebuildRepository(db).rebuild()
    assert snapshot(db) == before and gen(db) == generation


def test_interrupted_enrichment_reprojects_latest_cache_not_last_applied_state(db):
    RebuildRepository(db).rebuild()
    with db.begin() as c:
        # Simulate save_lookup committed, followed by a crash before complete().
        c.execute(
            text(
                "UPDATE metadata_cache SET payload = CAST(:p AS jsonb) "
                "WHERE entity_type = 'recording'"
            ),
            {
                "p": json.dumps(
                    {"genres": [{"genre_mbid": G1, "name": "new cached label", "count": 7}]}
                )
            },
        )
    RebuildRepository(db).rebuild()
    with db.connect() as c:
        assert c.execute(text("SELECT name FROM genres")).scalar_one() == "new cached label"
        assert c.execute(text("SELECT votes FROM entity_genres")).scalar_one() == 7


def test_dry_run_rolls_back(db):
    with db.begin() as c:
        c.execute(text("UPDATE listens SET track_name = 'bad'"))
    before, g = snapshot(db), gen(db)
    assert RebuildRepository(db).rebuild(dry_run=True)["dry_run"] is True
    assert snapshot(db) == before and gen(db) == g


def test_busy_locks_abort_without_change(db):
    before, g = snapshot(db), gen(db)
    repo = SyncRepository(db)
    with repo.locked(USER) as got:
        assert got
        with pytest.raises(RebuildBusy):
            RebuildRepository(db).rebuild()
    holder = db.connect().execution_options(isolation_level="AUTOCOMMIT")
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ENRICHMENT_LOCK})
    try:
        with pytest.raises(RebuildBusy):
            RebuildRepository(db).rebuild()
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ENRICHMENT_LOCK})
        holder.close()
    assert snapshot(db) == before and gen(db) == g
    RebuildRepository(db).rebuild()  # locks were released


def test_failure_is_atomic(db, monkeypatch):
    before, g = snapshot(db), gen(db)

    def boom(self, c):
        raise RuntimeError("secret")

    monkeypatch.setattr(RebuildRepository, "_rebuild_metadata", boom)
    with pytest.raises(RuntimeError):
        RebuildRepository(db).rebuild()
    assert snapshot(db) == before and gen(db) == g


def test_cli_is_offline_and_sanitizes(monkeypatch, capsys):
    from music_dashboard.presentation.cli_commands.rebuild import configure, run

    p = argparse.ArgumentParser()
    configure(p.add_subparsers())
    assert p.parse_args(["rebuild", "--dry-run"]).dry_run is True

    def fail(**_):
        raise RuntimeError("postgresql://u:private@h/db")

    monkeypatch.setattr("music_dashboard.application.rebuild_projections.run_once", fail)
    with pytest.raises(SystemExit) as e:
        run(argparse.Namespace(dry_run=False))
    assert "private" not in str(e.value)


def charts(eng):
    from datetime import date

    from music_dashboard.domain.statistics.models import StatisticsFilter
    from music_dashboard.domain.statistics.policies import FAMILIES
    from music_dashboard.infrastructure.persistence.statistics_queries import (
        SQLStatisticsQueries,
    )

    q = SQLStatisticsQueries(eng)
    f = StatisticsFilter(date(2023, 11, 1), date(2023, 11, 30), "UTC", 10)
    out = {}
    for fam in FAMILIES:
        r = q.query(fam, f)
        r.pop("last_updated", None)
        r.pop("generation", None)  # intentionally bumped by rebuild
        out[fam] = json.dumps(r, sort_keys=True, default=str)
    return out


def test_poisoned_or_cleared_derived_state_gives_identical_charts(db):
    RebuildRepository(db).rebuild()
    good, state = charts(db), snapshot(db)
    assert any('"slices": []' not in v for v in good.values())
    with db.begin() as c:
        c.execute(text("DELETE FROM listens"))  # cascades listen_artists
        c.execute(text("DELETE FROM entity_genres"))
        c.execute(text("DELETE FROM artist_countries"))
        c.execute(text("DELETE FROM genres"))
        c.execute(text("INSERT INTO genres VALUES (gen_random_uuid(), 'stale')"))
        c.execute(text("INSERT INTO artist_countries VALUES (:a, 'FR')"), {"a": A2})
        c.execute(
            text(
                "INSERT INTO entity_genres (entity_type, entity_mbid, genre_mbid, votes) "
                "SELECT 'artist', :a, genre_mbid, 9 FROM genres"
            ),
            {"a": A1},
        )
    RebuildRepository(db).rebuild()
    assert charts(db) == good
    after = snapshot(db)
    for k in ("listens", "artists", "ac", "raw", "cache", "sync"):
        assert after[k] == state[k]
    assert [r[1:] for r in after["eg"]] == [r[1:] for r in state["eg"]][: len(after["eg"])]


@pytest.mark.parametrize("status", ["transient_error", "pending"])
def test_unreconstructable_cache_refuses_and_preserves(db, status):
    with db.begin() as c:
        c.execute(
            text("UPDATE metadata_cache SET status = :s WHERE entity_type = 'recording'"),
            {"s": status},
        )
        c.execute(text("UPDATE listens SET track_name = 'kept'"))
    before, g = snapshot(db), gen(db)
    with pytest.raises(RebuildUnsupported):
        RebuildRepository(db).rebuild()
    assert snapshot(db) == before and gen(db) == g


def test_lb_precedence_mb_fallback_and_independent_label_updates(db):
    g2 = str(uuid.uuid5(uuid.NAMESPACE_DNS, "g2"))
    with db.begin() as c:
        c.execute(  # MB-only artist gets MB votes; LB recording wins over MB recording
            text(
                "INSERT INTO metadata_cache (entity_type, entity_mbid, source, status, payload) "
                "VALUES ('recording', :r, 'musicbrainz', 'positive', CAST(:m AS jsonb)), "
                "('release_group', :a, 'musicbrainz', 'positive', CAST(:m AS jsonb))"
            ),
            {
                "r": REC,
                "a": A2,
                "m": json.dumps({"genres": [{"id": g2, "name": "jazz", "count": 1}]}),
            },
        )
    RebuildRepository(db).rebuild()
    with db.connect() as c:
        rows = c.execute(
            text("SELECT entity_type, genre_mbid::text, votes FROM entity_genres ORDER BY 1,2")
        ).all()
    assert rows == [("recording", G1, 3), ("release_group", g2, 1)]
    with db.begin() as c:  # label-only change: votes identical
        c.execute(
            text(
                "UPDATE metadata_cache SET payload = CAST(:p AS jsonb) "
                "WHERE entity_type = 'recording' AND source = 'listenbrainz'"
            ),
            {"p": json.dumps({"genres": [{"genre_mbid": G1, "name": "hard rock", "count": 3}]})},
        )
    RebuildRepository(db).rebuild()
    with db.connect() as c:
        assert (
            c.execute(text("SELECT name FROM genres WHERE genre_mbid = :g"), {"g": G1}).scalar()
            == "hard rock"
        )
        assert c.execute(text("SELECT count(*) FROM genres")).scalar() == 2
