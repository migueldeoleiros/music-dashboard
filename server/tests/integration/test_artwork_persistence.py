"""Artwork migration, grants, representative-release rule and statistics joins (disposable DB)."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from music_dashboard.domain.artwork.entities import ArtworkOutcome, ArtworkWork
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.repositories.artwork import SqlArtworkRepository
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

from .conftest import alembic_cfg

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)
H = "c" * 32
DZ = f"https://cdn-images.dzcdn.net/images/cover/{H}/1000x1000-000000-80-0-0.jpg"
DZ_ARTIST = f"https://cdn-images.dzcdn.net/images/artist/{H}/1000x1000-000000-80-0-0.jpg"


def u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


RG, REL_A, REL_B, REL_C = u(100), u(1), u(2), u(3)  # REL_A < REL_B (tie), REL_C less listened
REC, REL_X, REL_Y = u(200), u(10), u(11)
ART, ART2 = u(300), u(301)


def scalar(eng, sql, **p):
    with eng.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def add_listen(c, n, *, rec=None, rel=None, rg=None, artist=None, track="T", album=None):
    row = c.execute(
        text(
            "INSERT INTO listens(user_name,listened_at,recording_msid,track_name,artist_name,"
            "release_name,recording_mbid,release_mbid,release_group_mbid) VALUES "
            "('art', timestamptz '2026-03-01T10:00Z' + make_interval(mins=>:n), :msid, :track,"
            " 'Artist', :album, CAST(:rec AS uuid), CAST(:rel AS uuid), CAST(:rg AS uuid)) "
            "RETURNING id"
        ),
        {
            "n": n,
            "msid": str(uuid.uuid4()),
            "track": track,
            "album": album,
            "rec": rec,
            "rel": rel,
            "rg": rg,
        },
    ).scalar_one()
    if artist:
        c.execute(
            text(
                "INSERT INTO listen_artists(listen_id,position,artist_name,artist_mbid) "
                "VALUES (:i,0,'Artist',CAST(:a AS uuid))"
            ),
            {"i": row, "a": artist},
        )


@pytest.fixture
def env(migrated_db, api_url):
    n = 0
    with migrated_db.begin() as c:
        for rel, times in ((REL_A, 2), (REL_B, 2), (REL_C, 1)):
            for _ in range(times):
                n += 1
                add_listen(c, n, rg=RG, rel=rel, album="Album", artist=ART)
        for rel, times in ((REL_Y, 1), (REL_X, 3)):  # X most listened for the recording
            for _ in range(times):
                n += 1
                add_listen(c, n, rec=REC, rel=rel, artist=ART2, track="Song")
        n += 1
        add_listen(c, n, track="Unmapped")  # name: keys only
        n += 1
        add_listen(c, n, rg=u(101), rel=None, album="No release", artist=None)
    api = create_engine(api_url)
    yield migrated_db, api
    api.dispose()


def flt(limit=100, country=None):
    return StatisticsFilter(date(2026, 1, 1), date(2026, 12, 31), "UTC", limit, country)


def items(result):
    return {i["key"]: i for i in result["items"]}


def test_representative_release_and_priority(env):
    admin, _ = env
    repo = SqlArtworkRepository(admin)
    due = {(w.entity_type, w.mbid): w for w in repo.due(NOW, 50)}
    assert due[("album", RG)].representative_release_mbid == REL_A  # tie -> smallest mbid
    assert due[("track", REC)].representative_release_mbid == REL_X  # most-listened release
    assert due[("album", u(101))].representative_release_mbid is None
    assert ("artist", ART) in due and ("artist", ART2) in due
    assert due[("album", RG)].listens == 5 and due[("track", REC)].listens == 4
    ordered = [w.listens for w in repo.due(NOW, 50)]
    assert ordered == sorted(ordered, reverse=True)
    assert len(repo.due(NOW, 2)) == 2  # bounded


def test_release_keyed_albums_and_lb_caa_hints_are_discovered(env):
    admin, _ = env
    rel_only, caa_rel, rec = u(400), u(401), u(402)
    with admin.begin() as c:
        for n, hint in ((900, True), (901, True), (902, False)):
            add_listen(c, n, rec=rec, rel=rel_only, album="Release only")
            payload = {
                "track_metadata": {
                    "mbid_mapping": {"caa_release_mbid": caa_rel, "caa_id": 4242 + n}
                    if hint
                    else None
                }
            }
            c.execute(
                text(
                    "INSERT INTO raw_listens(user_name,listened_at,recording_msid,payload,"
                    "provenance) SELECT user_name,listened_at,recording_msid,"
                    "CAST(:p AS jsonb),'{}' FROM listens WHERE listened_at = "
                    "timestamptz '2026-03-01T10:00Z' + make_interval(mins=>:n)"
                ),
                {"p": json.dumps(payload), "n": n},
            )
    due = {(w.entity_type, w.mbid): w for w in SqlArtworkRepository(admin).due(NOW, 100)}
    album = due[("album", rel_only)]  # keyed like the ranking: release MBID, no release group
    assert album.album_kind == "release" and album.representative_release_mbid == rel_only
    assert album.listens == 3 and album.caa_hint == (caa_rel, "5142")  # tie -> first sorted
    assert due[("track", rec)].caa_hint == (caa_rel, "5142")
    assert due[("album", RG)].album_kind == "release-group" and due[("album", RG)].caa_hint is None
    with admin.begin() as c:  # tombstoned raw rows never feed hints
        c.execute(text("UPDATE raw_listens SET tombstoned_at = now()"))
    again = {(w.entity_type, w.mbid): w for w in SqlArtworkRepository(admin).due(NOW, 100)}
    assert again[("track", rec)].caa_hint is None


def test_save_semantics_ttl_transient_and_generation(env):
    admin, _ = env
    repo = SqlArtworkRepository(admin)
    work = ArtworkWork("album", RG, REL_A, 5)
    retry = NOW + timedelta(days=30)
    assert repo.save(work, ArtworkOutcome("resolved", "deezer", DZ), NOW, retry) is True
    assert repo.save(work, ArtworkOutcome("resolved", "deezer", DZ), NOW, retry) is False
    assert ("album", RG) not in {(w.entity_type, w.mbid) for w in repo.due(NOW, 50)}
    # after the TTL it is due again
    assert ("album", RG) in {(w.entity_type, w.mbid) for w in repo.due(retry, 50)}
    # transient refresh keeps the resolved image, bumps attempts and retry schedule
    soon = NOW + timedelta(minutes=5)
    assert repo.save(work, ArtworkOutcome("transient"), NOW, soon) is False
    assert repo.attempts(work) == 1
    row = scalar(admin, "SELECT status || '|' || image_url FROM artwork WHERE entity_mbid=:m", m=RG)
    assert row == f"resolved|{DZ}"
    # miss replaces it (visible change) and resets attempts; reset_retries makes it due
    assert repo.save(work, ArtworkOutcome("miss"), NOW, NOW + timedelta(days=7)) is True
    assert repo.attempts(work) == 0
    assert ("album", RG) not in {(w.entity_type, w.mbid) for w in repo.due(NOW, 50)}
    assert repo.reset_retries() == 1
    assert ("album", RG) in {
        (w.entity_type, w.mbid) for w in repo.due(NOW + timedelta(seconds=1), 50)
    }
    before = scalar(admin, "SELECT enrichment_generation FROM data_generation WHERE id=1")
    repo.bump_generation()
    assert (
        scalar(admin, "SELECT enrichment_generation FROM data_generation WHERE id=1") == before + 1
    )


def test_enrichment_due_work_probe(env):
    admin, _ = env
    repo = SqlArtworkRepository(admin)
    assert repo.enrichment_has_due_work(NOW) is False
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO enrichment_queue(entity_type,entity_mbid) VALUES "
                "('artist', CAST(:m AS uuid))"
            ),
            {"m": ART},
        )
    assert repo.enrichment_has_due_work(NOW) is True


def test_check_constraints_reject_unsafe_rows(env):
    admin, _ = env
    bad = [
        ("album", "'deezer'", "'http://cdn-images.dzcdn.net/x'", "'resolved'"),  # not https
        ("album", "'deezer'", "NULL", "'resolved'"),  # resolved without url
        ("album", "'none'", f"'{DZ}'", "'resolved'"),  # resolved with source none
        ("album", "'none'", f"'{DZ}'", "'miss'"),  # miss must not carry a url
        ("playlist", "'none'", "NULL", "'miss'"),
        ("album", "'itunes'", f"'{DZ}'", "'resolved'"),
        ("album", "'none'", "NULL", "'bogus'"),
    ]
    for t, source, url, status in bad:
        with pytest.raises(DBAPIError):
            with admin.begin() as c:
                c.execute(
                    text(
                        f"INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status) "
                        f"VALUES ('{t}', CAST('{RG}' AS uuid), {source}, {url}, {status})"
                    )
                )
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,status) "
                f"VALUES ('album', CAST('{RG}' AS uuid), 'none', 'miss')"
            )
        )
    with pytest.raises(DBAPIError):  # primary key: one row per (type, mbid)
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO artwork(entity_type,entity_mbid,source,status) "
                    f"VALUES ('album', CAST('{RG}' AS uuid), 'none', 'miss')"
                )
            )


def test_api_role_reads_only_join_columns(env):
    admin, api_ro = env
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status) "
                f"VALUES ('album', CAST('{RG}' AS uuid), 'deezer', '{DZ}', 'resolved')"
            )
        )
    api = api_ro
    assert scalar(admin, "SELECT has_table_privilege('music_api','artwork','SELECT')") is False
    assert scalar(admin, "SELECT has_table_privilege('music_writer','artwork','INSERT')") is True
    assert scalar(admin, "SELECT has_table_privilege('music_writer','artwork','UPDATE')") is True
    for col in ("entity_type", "entity_mbid", "image_url", "status"):
        assert scalar(admin, f"SELECT has_column_privilege('music_api','artwork','{col}','SELECT')")
    for col in ("source", "fetched_at", "next_retry_at", "attempts"):
        assert not scalar(
            admin, f"SELECT has_column_privilege('music_api','artwork','{col}','SELECT')"
        )
    assert scalar(api, "SELECT image_url FROM artwork WHERE status='resolved'") == DZ
    for sql in (
        "SELECT * FROM artwork",
        "SELECT source FROM artwork",
        "SELECT attempts FROM artwork",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            scalar(api, sql)
    writable = create_engine(
        api.url, connect_args={"options": "-c default_transaction_read_only=off"}
    )
    try:
        for stmt in (
            "INSERT INTO artwork(entity_type,entity_mbid,source,status) "
            f"VALUES ('track','{REC}','none','miss')",
            "UPDATE artwork SET image_url = 'https://archive.org/x'",
            "DELETE FROM artwork",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with writable.begin() as c:
                    c.execute(text(stmt))
        # the API role still cannot read raw payloads
        with pytest.raises(DBAPIError, match="permission denied"):
            scalar(writable, "SELECT count(*) FROM raw_listens")
    finally:
        writable.dispose()


def test_downgrade_and_reupgrade(fresh_db, admin_url):
    cfg = alembic_cfg(admin_url)
    command.upgrade(cfg, "head")
    assert scalar(fresh_db, "SELECT to_regclass('public.artwork') IS NOT NULL") is True
    command.downgrade(cfg, "0002")
    assert scalar(fresh_db, "SELECT to_regclass('public.artwork') IS NULL") is True
    command.upgrade(cfg, "head")
    assert scalar(fresh_db, "SELECT to_regclass('public.artwork') IS NOT NULL") is True


def put(admin, kind, mbid, url, source="deezer"):
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status) "
                "VALUES (:t, CAST(:m AS uuid), :s, :u, 'resolved')"
            ),
            {"t": kind, "m": mbid, "s": source, "u": url},
        )


def test_ranking_joins_fill_image_url_without_changing_totals(env):
    admin, api = env
    q = SQLStatisticsQueries(api)
    baseline = {
        f: q.query(f"rankings/{f}", flt()) for f in ("albums", "tracks", "artists", "genres")
    }
    assert all(i["image_url"] is None for r in baseline.values() for i in r["items"])
    caa = f"https://coverartarchive.org/release-group/{RG}/front"
    put(admin, "album", RG, caa, "caa")
    put(admin, "track", REC, DZ)
    put(admin, "artist", ART, DZ_ARTIST)
    # a row of the wrong entity type for the same mbid must never leak across dimensions
    put(admin, "artist", RG, DZ_ARTIST)
    with admin.begin() as c:  # new generation invalidates the response cache
        c.execute(
            text("UPDATE data_generation SET enrichment_generation = enrichment_generation + 1")
        )
    after = {f: q.query(f"rankings/{f}", flt()) for f in ("albums", "tracks", "artists", "genres")}
    for family, result in after.items():
        old = baseline[family]
        assert [(i["key"], i["listens"], i["share"], i["label"]) for i in result["items"]] == [
            (i["key"], i["listens"], i["share"], i["label"]) for i in old["items"]
        ]  # no duplication, same order and totals
        assert result["coverage"] == old["coverage"]
    assert items(after["albums"])[RG]["image_url"] == caa
    assert items(after["tracks"])[REC]["image_url"] == DZ
    assert items(after["artists"])[ART]["image_url"] == DZ_ARTIST
    assert items(after["artists"])[ART2]["image_url"] is None  # unresolved -> placeholder
    for family in ("albums", "tracks", "artists"):
        for key, item in items(after[family]).items():
            if key.startswith("name:"):
                assert item["image_url"] is None
    assert all(i["image_url"] is None for i in after["genres"]["items"])
    # with a small limit the Other bucket is unchanged and image_url only on listed items
    small = q.query("rankings/albums", flt(limit=1))
    assert [i["key"] for i in small["items"]] == [RG] and small["items"][0]["image_url"] == caa


def test_non_resolved_and_unsafe_urls_are_never_returned(env):
    admin, api = env
    q = SQLStatisticsQueries(api)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,status) "
                f"VALUES ('album', CAST('{RG}' AS uuid), 'none', 'miss')"
            )
        )
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,status) "
                f"VALUES ('track', CAST('{REC}' AS uuid), 'none', 'transient')"
            )
        )
    # https but non-allowlisted host: the DB accepts it, the response must not
    put(admin, "artist", ART, "https://evil-archive.org/x.jpg")
    put(admin, "artist", ART2, "https://archive.org.evil.com/x.jpg")
    result = {f: q.query(f"rankings/{f}", flt()) for f in ("albums", "tracks", "artists")}
    assert all(i["image_url"] is None for r in result.values() for i in r["items"])


def test_country_artists_join_matches_countries_totals(env):
    admin, api = env
    q = SQLStatisticsQueries(api)
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO artist_countries(artist_mbid,country_code) VALUES "
                "(CAST(:a AS uuid),'US'),(CAST(:b AS uuid),'US')"
            ),
            {"a": ART, "b": ART2},
        )
    base = q.query("countries/artists", replace(flt(), country_code="US"))
    assert {i["key"] for i in base["items"]} == {ART, ART2}
    assert all(i["image_url"] is None for i in base["items"])
    put(admin, "artist", ART, DZ_ARTIST)
    with admin.begin() as c:
        c.execute(
            text("UPDATE data_generation SET enrichment_generation = enrichment_generation + 1")
        )
    after = q.query("countries/artists", replace(flt(), country_code="US"))
    assert [(i["key"], i["listens"], i["share"]) for i in after["items"]] == [
        (i["key"], i["listens"], i["share"]) for i in base["items"]
    ]
    assert items(after)[ART]["image_url"] == DZ_ARTIST and items(after)[ART2]["image_url"] is None
    totals = {c["code"]: c["listens"] for c in q.query("countries", flt())["countries"]}
    assert sum(i["listens"] for i in after["items"]) == pytest.approx(totals["US"])
