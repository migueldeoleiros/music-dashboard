"""Statistics proof uses only synthetic fixtures and a disposable local PostgreSQL."""

import json
import random
import statistics
import time
import uuid
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from music_dashboard.app import app
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    DIMENSIONS,
    FAMILIES,
    SUPPORTED_FAMILIES,
    InvalidStatisticsRequest,
)
from music_dashboard.infrastructure.persistence.seed import seed_fixtures
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries
from music_dashboard.presentation.api.dependencies import get_statistics, statistics_queries
from music_dashboard.presentation.api.schemas.statistics import (
    CountryResponse,
    EvolutionResponse,
    HealthResponse,
    HeatmapResponse,
    PieResponse,
    RankingResponse,
)

pytestmark = pytest.mark.integration


def test_wide_custom_evolution_range_limit_100_is_bounded(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(
            text("""
          INSERT INTO listens(user_name,listened_at,recording_msid,artist_name)
          SELECT 'statistics-wide',timestamptz '2026-01-05T00:00Z',
                 md5('wide-'||g)::uuid,'Wide artist '||g
          FROM generate_series(1,100) g
        """)
        )
    from music_dashboard.domain.statistics.policies import InvalidStatisticsRequest

    with pytest.raises(InvalidStatisticsRequest, match="evolution value budget"):
        q.query("evolution/artists", filters("0002-01-01", "9998-12-31", limit=100))


def test_repeated_midnight_fixed_utc_regression(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        for at in (
            "2025-11-02T03:59:59Z",
            "2025-11-02T04:00:00Z",
            "2025-11-02T04:30:00Z",
            "2025-11-02T05:00:00Z",
            "2025-11-02T05:30:00Z",
            "2025-11-03T04:59:59Z",
            "2025-11-03T05:00:00Z",
        ):
            c.execute(
                text("""
              INSERT INTO listens(user_name,listened_at,recording_msid,artist_name)
              VALUES ('statistics-test',CAST(:at AS timestamptz),CAST(:id AS uuid),'Havana')
            """),
                {"at": at, "id": str(uuid.uuid4())},
            )
    for day, expected in (("2025-11-01", 1), ("2025-11-02", 5), ("2025-11-03", 1)):
        response = q.query("heatmap", filters(day, tz="America/Havana"))
        assert sum(r["listens"] for r in response["cells"]) == expected
        assert all(r["day"] == date.fromisoformat(day) for r in response["cells"])


def test_missing_artist_never_other_and_empty_database(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        for name in ("Named unmapped", "Known tail", None):
            c.execute(
                text("""
              INSERT INTO listens(user_name,listened_at,recording_msid,artist_name)
              VALUES ('statistics-test','2026-01-05T00:00:00Z',CAST(:id AS uuid),:name)
            """),
                {"id": str(uuid.uuid4()), "name": name},
            )
    pie = q.query("pies/artists", filters(limit=1))
    PieResponse.model_validate(pie)
    assert pie["coverage"]["artists"]["included_listens"] == 2
    assert pie["coverage"]["artists"]["excluded_listens"] == 1
    assert sum(s["listens"] for s in pie["slices"]) == 2
    assert next(s for s in pie["slices"] if s["is_other"])["listens"] == 1
    evolution = q.query("evolution/artists", filters(limit=1))
    assert sum(sum(s["values"]) for s in evolution["series"]) == 2
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    empty = q.query("evolution/artists", filters("0001-01-01", "9999-12-31"))
    assert empty["buckets"] == empty["series"] == []
    assert q.health()["coverage"]["listens"]["total_listens"] == 0


def test_representative_cold_alltime_and_health_load(stats, capsys):
    q, writer = stats
    with writer.begin() as c:
        c.execute(
            text("""
          INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,track_name,
                              release_name,recording_mbid,release_group_mbid)
          SELECT 'statistics-scale', timestamptz '2010-01-01 00:00Z'+g*interval '1 hour',
                 md5('statistics-scale-'||g)::uuid, 'Scale artist '||(g%1000),
                 'Scale track '||(g%10000), 'Scale album '||(g%1000),
                 (SELECT recording_mbid FROM listens WHERE recording_mbid IS NOT NULL LIMIT 1),
                 (SELECT release_group_mbid FROM listens
                  WHERE release_group_mbid IS NOT NULL LIMIT 1)
          FROM generate_series(1,100000) g
        """)
        )
        c.execute(
            text("""
          INSERT INTO listen_artists(listen_id,position,artist_name,artist_mbid)
          SELECT id,0,artist_name,(SELECT artist_mbid FROM artist_countries
                                  WHERE country_code='US' LIMIT 1)
          FROM listens WHERE user_name='statistics-scale'
        """)
        )
        c.execute(text("ANALYZE listens"))
        c.execute(text("ANALYZE listen_artists"))
    measurements = {}
    for family in ("rankings/artists", "pies/genres", "evolution/genres", "heatmap", "health"):
        cold = SQLStatisticsQueries(q.engine)
        begin = time.perf_counter()
        response = (
            cold.health()
            if family == "health"
            else cold.query(family, filters("0001-01-01", "9999-12-31"))
        )
        measurements[family] = round((time.perf_counter() - begin) * 1000, 3)
        assert response["coverage"]["listens"]["total_listens"] == 100030
        assert_coverage(response)
        if family == "health":
            start = time.perf_counter()
            cold.health()
            measurements["health_warm"] = round((time.perf_counter() - start) * 1000, 3)
    with capsys.disabled():
        print("\nSTATISTICS_COLD_100030 " + json.dumps(measurements))


def filters(start="2026-01-05", end=None, tz="UTC", limit=10):
    return StatisticsFilter(date.fromisoformat(start), date.fromisoformat(end or start), tz, limit)


@pytest.fixture
def stats(migrated_db, api_url):
    seed_fixtures(migrated_db)
    engine = create_engine(api_url)
    yield SQLStatisticsQueries(engine), migrated_db
    engine.dispose()


def assert_coverage(response):
    assert set(response["coverage"]) == set(DIMENSIONS)
    for c in response["coverage"].values():
        assert c["included_listens"] + c["excluded_listens"] == c["total_listens"]


def test_all_endpoints_real_role_and_contract(stats):
    q, _ = stats
    app.dependency_overrides[statistics_queries] = lambda: q
    # get_statistics resolves its provider directly, so override the use case too.
    from music_dashboard.application.get_statistics import GetStatistics

    app.dependency_overrides[get_statistics] = lambda: GetStatistics(q)
    try:
        client = TestClient(app)
        for family in sorted(SUPPORTED_FAMILIES):
            response = client.get(
                "/api/countries/US/artists" if family == "countries/artists" else f"/api/{family}",
                params={"from": "2026-01-05", "to": "2026-01-05", "tz": "UTC"},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["fixture"] is False
            assert_coverage(data)
        health = client.get("/api/health")
        assert health.status_code == 200, health.text
        assert health.json()["mode"] == "live"
        HealthResponse.model_validate(health.json())
    finally:
        app.dependency_overrides.clear()


def test_fixture_weights_and_fallback_precedence(stats):
    q, _ = stats
    data = q.query("rankings/genres", filters())
    RankingResponse.model_validate(data)
    assert data["coverage"]["genres"]["included_listens"] == 4
    # recording 0.75 rock + 0.25 pop; rg .8 jazz + .2 folk;
    # artist duo .5 electronic + .25 rock + .25 pop; zero votes .5 jazz + .5 folk.
    values = {r["label"]: r["listens"] for r in data["items"]}
    assert values == pytest.approx(
        {"Rock": 1, "Pop": 0.5, "Electronic": 0.5, "Jazz": 1.3, "Folk": 0.7}
    )
    for family, field in (
        ("pies/genres", "slices"),
        ("countries", "countries"),
        ("pies/artists", "slices"),
    ):
        response = q.query(family, filters(limit=1))
        assert_coverage(response)
        dimension = "countries" if family == "countries" else family.split("/")[1]
        assert sum(r["listens"] for r in response[field]) == pytest.approx(
            response["coverage"][dimension]["included_listens"]
        )
        assert sum(r["share"] for r in response[field]) == pytest.approx(1)
    countries = q.query("countries", filters())
    CountryResponse.model_validate(countries)
    assert countries["coverage"]["countries"]["included_listens"] == 4
    assert {r["code"]: r["listens"] for r in countries["countries"]} == pytest.approx(
        {"US": 1.5, "RO": 2, "JP": 0.5}
    )
    feb = q.query("countries", filters("2026-02-11"))
    assert [r["code"] for r in feb["countries"]] == ["RO"]
    assert feb["coverage"]["countries"]["included_listens"] == 1


def test_country_artists_reconcile_with_countries(stats):
    q, db = stats
    for start, end in (("2026-01-05", None), ("2026-02-11", None), ("2026-01-01", "2026-12-31")):
        flt = filters(start, end, limit=100)
        countries = q.query("countries", flt)
        assert countries["countries"], start
        for row in countries["countries"]:
            r = q.query("countries/artists", replace(flt, country_code=row["code"]))
            RankingResponse.model_validate(r)
            assert r["dimension"] == "artists"
            assert r["coverage"] == countries["coverage"]
            assert all(i["mbid"] and i["image_url"] is None for i in r["items"])
            assert all(i["image_url"] is None for i in q.query("rankings/artists", flt)["items"])
            # Totals AND shares reconcile with /api/countries for identical filters.
            assert sum(i["listens"] for i in r["items"]) == pytest.approx(row["listens"])
            assert sum(i["share"] for i in r["items"]) == pytest.approx(row["share"])
            assert [i["listens"] for i in r["items"]] == sorted(
                (i["listens"] for i in r["items"]), reverse=True
            )
    # Per-code cache keys: same filters, different codes, never conflated (and repeatable).
    flt = filters(limit=100)
    us = q.query("countries/artists", replace(flt, country_code="US"))
    jp = q.query("countries/artists", replace(flt, country_code="JP"))
    assert us["items"] != jp["items"]
    assert q.query("countries/artists", replace(flt, country_code="US")) == us
    # Countries with no listens, and pseudo-countries, are empty / rejected.
    assert q.query("countries/artists", replace(flt, country_code="FR"))["items"] == []
    for bad in ("XW", "XE", "ZZ", "us"):
        with pytest.raises(InvalidStatisticsRequest):
            q.query("countries/artists", replace(flt, country_code=bad))


def test_country_artists_split_credits_and_exclude_pseudo_countries(stats):
    q, db = stats
    with db.begin() as c:
        c.execute(text("UPDATE artist_countries SET country_code='US'"))
        c.execute(
            text(
                "UPDATE artist_countries SET country_code='XW' WHERE artist_mbid="
                "(SELECT artist_mbid FROM artist_countries ORDER BY artist_mbid LIMIT 1)"
            )
        )
    flt = filters("2026-01-01", "2026-12-31", limit=100)
    countries = q.query("countries", flt)
    assert {r["code"] for r in countries["countries"]} == {"US"}
    us = q.query("countries/artists", replace(flt, country_code="US"))
    assert sum(i["listens"] for i in us["items"]) == pytest.approx(
        countries["countries"][0]["listens"]
    )
    with pytest.raises(InvalidStatisticsRequest):
        q.query("countries/artists", replace(flt, country_code="XW"))


@pytest.mark.parametrize(
    "start,tz,expected",
    [
        ("2026-03-08", "America/New_York", 2),
        ("2026-11-01", "America/New_York", 2),
        ("2026-03-29", "Europe/Chisinau", 3),
        ("2026-10-25", "Europe/Chisinau", 3),
        ("2026-01-16", "Asia/Kolkata", 1),
        ("2026-01-16", "Asia/Kathmandu", 3),
        ("2011-12-30", "Pacific/Apia", 0),
        ("2011-12-31", "Pacific/Apia", 1),
    ],
)
def test_timezone_seeded_edges(stats, start, tz, expected):
    q, _ = stats
    result = q.query("heatmap", filters(start, tz=tz))
    HeatmapResponse.model_validate(result)
    assert sum(r["listens"] for r in result["cells"]) == expected
    assert all(r["day"] == date.fromisoformat(start) for r in result["cells"])


@pytest.mark.parametrize(
    "day,tz,hours",
    [
        ("2026-03-08", "America/New_York", 23),
        ("2026-11-01", "America/New_York", 25),
        ("2018-11-04", "America/Sao_Paulo", 23),
        ("2011-12-30", "Pacific/Apia", 0),
        ("2026-01-16", "Asia/Kolkata", 24),
        ("2026-01-16", "Asia/Kathmandu", 24),
    ],
)
def test_exact_boundaries_computed_in_postgres(stats, day, tz, hours):
    q, writer = stats
    # Extra task-owned synthetic rows; all boundary arithmetic remains in SQL.
    with writer.begin() as c:
        measured = c.execute(
            text("""
          SELECT extract(epoch FROM (
           ((CAST(:day AS date)+1)::timestamp AT TIME ZONE :tz) -
            (CAST(:day AS date)::timestamp AT TIME ZONE :tz)))/3600
        """),
            {"day": day, "tz": tz},
        ).scalar_one()
        assert measured == hours
        c.execute(text("DELETE FROM listens"))
        for edge in ("start", "last", "end"):
            c.execute(
                text("""
              INSERT INTO listens (user_name,listened_at,recording_msid,artist_name,track_name)
              SELECT 'statistics-test', CASE :edge
                WHEN 'start' THEN CAST(:day AS date)::timestamp AT TIME ZONE :tz
                WHEN 'last' THEN ((CAST(:day AS date)+1)::timestamp AT TIME ZONE :tz)
                                - interval '1 second'
                ELSE (CAST(:day AS date)+1)::timestamp AT TIME ZONE :tz END,
                CAST(:msid AS uuid), 'Boundary artist','Boundary track'
            """),
                {"edge": edge, "day": day, "tz": tz, "msid": str(uuid.uuid4())},
            )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    response = q.query("heatmap", filters(day, tz=tz))
    assert response["coverage"]["listens"]["total_listens"] == (2 if hours else 0)


def test_alias_alltime_empty_and_generation_invalidation(stats):
    q, writer = stats
    a = q.query("heatmap", filters("2026-01-16", tz="Asia/Calcutta"))
    b = q.query("heatmap", filters("2026-01-16", tz="Asia/Kolkata"))
    assert a == b
    assert len(q.cache.entries) == 2  # one canonical chart plus shared coverage
    alltime = filters("0001-01-01", "9999-12-31")
    all_response = q.query("heatmap", alltime)
    with writer.connect() as c:
        total = c.execute(text("SELECT count(*) FROM listens")).scalar_one()
    assert sum(r["listens"] for r in all_response["cells"]) == total
    for family in FAMILIES:
        data = q.query(family, filters("1999-01-01"))
        assert_coverage(data)
        assert data["coverage"]["listens"]["total_listens"] == 0
    first = q.query("pies/genres", filters())
    second = q.query("pies/genres", filters())
    assert first == second
    with writer.begin() as c:
        c.execute(text("DELETE FROM entity_genres"))
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    empty = q.query("pies/genres", filters())
    assert empty["generation"] == first["generation"] + 1
    assert empty["slices"] == []
    assert empty["coverage"]["genres"]["excluded_listens"] == 6
    assert empty["coverage"]["listens"]["total_listens"] == 6


def test_evolution_zero_fill_other_and_partial(stats):
    q, _ = stats
    daily = q.query("evolution/genres", filters("2026-01-04", "2026-01-07", limit=1))
    EvolutionResponse.model_validate(daily)
    assert daily["bucket_unit"] == "day"
    assert len(daily["buckets"]) == 4
    assert sum(s["values"][1] for s in daily["series"]) == pytest.approx(4)
    assert all(s["values"][0] == s["values"][2] == s["values"][3] == 0 for s in daily["series"])
    assert sum(s["is_other"] for s in daily["series"]) == 1
    assert next(s for s in daily["series"] if not s["is_other"])["label"] == "Jazz"
    weekly = q.query("evolution/artists", filters("2026-01-01", "2026-05-02", limit=1))
    assert weekly["bucket_unit"] == "week"
    assert weekly["buckets"][-1]["partial"] is True
    assert weekly["buckets"][-1]["end"] == date(2026, 5, 3)
    assert all(b["start"].weekday() == 0 for b in weekly["buckets"][1:])
    monthly = q.query("evolution/artists", filters("0001-01-01", "9999-12-31", limit=1))
    assert monthly["bucket_unit"] == "month"
    assert sum(sum(s["values"]) for s in monthly["series"]) == pytest.approx(30)


def test_unicode_identity_and_missing_states(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        for name in ("Straße", "STRASSE", "ＳＴＲＡＳＳＥ", "Band feat. Guest & Duo"):
            c.execute(
                text("""
              INSERT INTO listens(user_name,listened_at,recording_msid,artist_name)
              VALUES ('statistics-test','2026-01-05T12:00:00Z',CAST(:msid AS uuid),:name)
            """),
                {"msid": str(uuid.uuid4()), "name": name},
            )
    artists = q.query("rankings/artists", filters())
    assert {r["key"]: r["listens"] for r in artists["items"]} == {
        "name:strasse": 3,
        "name:band feat. guest & duo": 1,
    }
    for dimension in ("tracks", "albums"):
        response = q.query(f"rankings/{dimension}", filters())
        assert all(r["metadata_state"] == "missing" for r in response["items"])
        assert sum(r["listens"] for r in response["items"]) == 4


def test_zero_and_full_enrichment_and_rebuild_determinism(stats):
    q, writer = stats
    # Fixed raw/cache -> repeated foundation rebuild gives identical chart payloads,
    # except the explicitly mutable freshness timestamp.
    before = {f: q.query(f, filters()) for f in FAMILIES}
    with writer.begin() as c:
        fixed_raw = c.execute(text("SELECT count(*) FROM raw_listens")).scalar_one()
        fixed_cache = c.execute(text("SELECT count(*) FROM metadata_cache")).scalar_one()
        c.execute(text("DELETE FROM listens"))
    seed_fixtures(writer)
    with writer.connect() as c:
        assert c.execute(text("SELECT count(*) FROM raw_listens")).scalar_one() == fixed_raw
        assert c.execute(text("SELECT count(*) FROM metadata_cache")).scalar_one() == fixed_cache
    rebuilt = SQLStatisticsQueries(q.engine)
    after = {f: rebuilt.query(f, filters()) for f in FAMILIES}
    for f in FAMILIES:
        before[f].pop("last_updated")
        after[f].pop("last_updated")
        assert before[f] == after[f]
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens WHERE recording_mbid IS NULL"))
        # Make every remaining recording resolve genres and every credit resolve a country.
        c.execute(
            text("""
          INSERT INTO entity_genres(entity_type,entity_mbid,genre_mbid,votes)
          SELECT DISTINCT 'recording', recording_mbid,(SELECT genre_mbid FROM genres LIMIT 1),1
          FROM listens ON CONFLICT DO NOTHING
        """)
        )
        c.execute(text("UPDATE artist_countries SET country_code='US'"))
        c.execute(
            text("""
          INSERT INTO artist_countries(artist_mbid,country_code)
          SELECT DISTINCT artist_mbid,'US' FROM listen_artists WHERE artist_mbid IS NOT NULL
          ON CONFLICT DO NOTHING
        """)
        )
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    full = q.query("pies/genres", filters())
    assert full["coverage"]["genres"]["excluded_listens"] == 0
    assert full["coverage"]["countries"]["excluded_listens"] == 0
    assert sum(s["listens"] for s in full["slices"]) == pytest.approx(5)


def test_role_no_raw_or_sync_tables(stats):
    q, _ = stats
    for table in ("raw_listens", "sync_runs", "sync_state", "metadata_cache"):
        with q.engine.connect() as c:
            allowed = c.execute(
                text("SELECT has_table_privilege(current_user,:table,'SELECT')"), {"table": table}
            ).scalar_one()
            assert not allowed
    with q.engine.connect() as c:
        assert c.execute(text("SELECT current_user")).scalar_one() == "music_api"
        assert c.execute(text("SHOW statement_timeout")).scalar_one() == "5s"


def test_small_random_timezone_range_load(stats, capsys):
    q, _ = stats
    rng = random.Random(73)
    zones = [
        "UTC",
        "Asia/Kolkata",
        "Asia/Kathmandu",
        "America/New_York",
        "Pacific/Apia",
        "Europe/Chisinau",
    ]
    timings = []
    families = sorted(FAMILIES)
    for _ in range(40):
        start, end = sorted(rng.sample(range(1, 29), 2))
        f = filters(
            f"2026-01-{start:02}", f"2026-01-{end:02}", rng.choice(zones), rng.choice([1, 8, 10])
        )
        begin = time.perf_counter()
        response = q.query(rng.choice(families), f)
        timings.append((time.perf_counter() - begin) * 1000)
        assert_coverage(response)
    with capsys.disabled():
        print(
            "\nSTATISTICS_LOAD "
            + json.dumps(
                {
                    "queries": len(timings),
                    "fixture_listens": 30,
                    "median_ms": round(statistics.median(timings), 3),
                    "max_ms": round(max(timings), 3),
                    "total_ms": round(sum(timings), 3),
                }
            )
        )
