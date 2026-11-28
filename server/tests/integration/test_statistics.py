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
    SummaryResponse,
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
    # Both artists hold 50% (> 2%), so nothing is folded into Other; missing never is.
    assert not any(s["is_other"] for s in pie["slices"])
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
    for family in (
        "rankings/artists",
        "rankings/albums",
        "pies/genres",
        "evolution/genres",
        "heatmap",
        "health",
    ):
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
    # Rankings only: one full count per distinct genre per listen (votes ignored, the
    # recording's -1 Folk tag included): recording Rock+Pop+Folk; rg Jazz+Folk; artist duo
    # Electronic+Rock+Pop; zero-vote Jazz+Folk.
    values = {r["label"]: r["listens"] for r in data["items"]}
    assert values == {"Rock": 2, "Pop": 2, "Electronic": 1, "Jazz": 2, "Folk": 3}
    for item in data["items"]:
        assert item["share"] == pytest.approx(item["listens"] / 10)  # 10 appearances
    # Pies/evolution stay vote-weighted (main behaviour):
    # recording 0.75 rock + 0.25 pop; rg .8 jazz + .2 folk;
    # artist duo .5 electronic + .25 rock + .25 pop; zero votes .5 jazz + .5 folk.
    pie = q.query("pies/genres", filters())
    assert {s["label"]: s["listens"] for s in pie["slices"]} == pytest.approx(
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
    # Genres Jazz 1.3, Rock 1, Folk .7, Pop .5, Electronic .5 of 4 are all above 2%: no Other.
    assert [s["label"] for s in daily["series"]] == ["Jazz", "Rock", "Folk", "Electronic", "Pop"]
    assert not any(s["is_other"] for s in daily["series"])
    weekly = q.query("evolution/artists", filters("2026-01-01", "2026-05-02", limit=1))
    assert weekly["bucket_unit"] == "week"
    assert weekly["buckets"][-1]["partial"] is True
    assert weekly["buckets"][-1]["end"] == date(2026, 5, 3)
    assert all(b["start"].weekday() == 0 for b in weekly["buckets"][1:])
    monthly = q.query("evolution/artists", filters("0001-01-01", "9999-12-31", limit=1))
    assert monthly["bucket_unit"] == "month"
    assert sum(sum(s["values"]) for s in monthly["series"]) == pytest.approx(30)


def seed_shares(writer, counts: dict[str, int], days: int = 3):
    """One named, unmapped artist per entry; its listens are spread over `days` local days."""
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        n = 0
        for name, count in counts.items():
            for _ in range(count):
                c.execute(
                    text("""
                  INSERT INTO listens(user_name,listened_at,recording_msid,artist_name)
                  VALUES ('share-test',timestamptz '2026-01-05T12:00Z' + make_interval(days => :d),
                          CAST(:id AS uuid),:name)
                """),
                    {"d": n % days, "id": str(uuid.uuid4()), "name": name},
                )
                n += 1
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))


def share_view(q, start="2026-01-05", end="2026-01-07"):
    pie = q.query("pies/artists", filters(start, end, limit=1))
    evolution = q.query("evolution/artists", filters(start, end, limit=1))
    return pie, evolution


def test_more_than_ten_qualifying_artists_are_never_truncated(stats):
    q, writer = stats
    counts = {f"Major {i:02d}": 16 for i in range(12)}  # 12 x 8% = 96%
    counts.update({f"Tail {i}": 1 for i in range(8)})  # 8 x 0.5%
    seed_shares(writer, counts)
    for limit in (1, 10, 100):  # limit no longer selects anything
        pie = q.query("pies/artists", filters("2026-01-05", "2026-01-07", limit=limit))
        evolution = q.query("evolution/artists", filters("2026-01-05", "2026-01-07", limit=limit))
        PieResponse.model_validate(pie)
        EvolutionResponse.model_validate(evolution)
        real = [s for s in pie["slices"] if not s["is_other"]]
        assert len(real) == 12
        assert [s["label"] for s in real] == [f"Major {i:02d}" for i in range(12)]
        assert pie["slices"][-1]["is_other"] and pie["slices"][-1]["listens"] == 8
        assert len(evolution["series"]) == 13 and evolution["series"][-1]["is_other"]
        assert [s["label"] for s in evolution["series"][:-1]] == [s["label"] for s in real]


def test_pie_one_percent_boundary_is_inclusive_and_conserves_listens(stats):
    q, writer = stats
    counts = {f"Five {i:02d}": 5 for i in range(18)}  # 90
    counts.update({"Four": 4, "Three": 3, "Exactly two": 2, "One": 1})  # total 100
    seed_shares(writer, counts)
    pie, evolution = share_view(q)
    assert pie["coverage"]["artists"]["included_listens"] == 100
    labels = {s["label"]: s for s in pie["slices"]}
    assert "One" in labels and "Exactly two" in labels  # exactly 1% is kept; 2% too
    assert "Other" not in labels  # nothing left to fold
    assert sum(s["listens"] for s in pie["slices"]) == 100
    # Evolution uses the same rule: exactly 1% and 2% are named series, nothing folds into Other.
    assert {s["label"] for s in evolution["series"]} == {
        *(f"Five {i:02d}" for i in range(18)),
        "Four",
        "Three",
        "Exactly two",
        "One",
    }
    # Just under 1% (1 of 101) goes to Other; conservation holds.
    seed_shares(writer, {**counts, "Extra": 1})
    pie, _ = share_view(q)
    assert pie["coverage"]["artists"]["included_listens"] == 101
    labels = {s["label"]: s for s in pie["slices"]}
    assert "One" not in labels and "Extra" not in labels
    assert labels["Other"]["listens"] == 2
    assert sum(s["listens"] for s in pie["slices"]) == 101
    _, evolution = share_view(q)  # evolution folds the same tail into Other
    other = next(s for s in evolution["series"] if s["is_other"])
    assert sum(other["values"]) == 2
    assert {s["label"] for s in evolution["series"] if not s["is_other"]} == {
        s["label"] for s in pie["slices"] if not s["is_other"]
    }
    assert sum(s["share"] for s in pie["slices"]) == pytest.approx(1)
    # Fractional-free ratio boundary: 1 of 100 is kept, 1 of 101 is not.
    seed_shares(writer, {"Solo": 1, **{f"Big {i}": 33 for i in range(3)}})  # 1/100
    pie, _ = share_view(q)
    assert "Solo" in {s["label"] for s in pie["slices"]}


def test_evolution_one_percent_boundary_is_inclusive_like_the_pie(stats):
    q, writer = stats
    counts = {f"Five {i:02d}": 5 for i in range(18)}  # 90
    counts.update({"Four": 4, "Three": 3, "Exactly two": 2, "One": 1})  # total 100
    seed_shares(writer, counts)
    _, evolution = share_view(q)
    names = {s["label"] for s in evolution["series"]}
    assert {"Four", "Three", "Exactly two", "One"} <= names  # 4%, 3%, 2% and exactly 1% qualify
    assert not any(s["is_other"] for s in evolution["series"])
    # Just under 1% (1 of 101) is folded into Other, together with the other sub-1% entry.
    seed_shares(writer, {**counts, "Extra": 1})
    _, evolution = share_view(q)
    names = {s["label"] for s in evolution["series"]}
    assert "One" not in names and "Extra" not in names and "Exactly two" in names
    other = next(s for s in evolution["series"] if s["is_other"])
    assert sum(other["values"]) == 2
    # Pure ratio boundary: 1 of 100 is exactly 1% (kept); 1 of 101 is not.
    seed_shares(writer, {"Solo": 1, **{f"Big {i}": 33 for i in range(3)}})  # 1/100
    _, evolution = share_view(q)
    assert "Solo" in {s["label"] for s in evolution["series"]}
    seed_shares(writer, {"Solo": 1, **{f"Big {i}": 33 for i in range(3)}, "Mid": 1})  # 1/101
    _, evolution = share_view(q)
    assert "Solo" not in {s["label"] for s in evolution["series"]}


def test_share_grouping_conserves_totals_zero_fills_and_is_stable(stats):
    q, writer = stats
    counts = {
        "Alpha": 30,
        "Beta": 30,
        "Gamma": 20,
        "Delta": 10,
        "Eps": 4,
        "Zeta": 3,
        "Eta": 2,
        "Theta": 1,
    }
    seed_shares(writer, counts, days=2)
    pie, evolution = share_view(q, "2026-01-04", "2026-01-08")
    included = pie["coverage"]["artists"]["included_listens"]
    assert included == 100
    assert sum(s["listens"] for s in pie["slices"]) == pytest.approx(included)
    assert sum(s["share"] for s in pie["slices"]) == pytest.approx(1)
    assert sum(sum(s["values"]) for s in evolution["series"]) == pytest.approx(included)
    for s in pie["slices"]:  # evolution agrees per entity where it shows the entity itself
        series = next((x for x in evolution["series"] if x["key"] == s["key"]), None)
        if series is not None:
            assert sum(series["values"]) == pytest.approx(s["listens"])
    # Zero fill: five daily buckets (4..8 Jan), listens only on the 5th and 6th.
    assert len(evolution["buckets"]) == 5
    assert all(len(s["values"]) == 5 for s in evolution["series"])
    assert all(
        s["values"][0] == 0 and s["values"][3] == s["values"][4] == 0 for s in evolution["series"]
    )
    # Pie: every entity >= 1% is named; listens desc, key tie-break (Alpha/Beta tie); no Other.
    assert [s["label"] for s in pie["slices"]] == [
        "Alpha", "Beta", "Gamma", "Delta", "Eps", "Zeta", "Eta", "Theta",
    ]  # fmt: skip
    # Evolution uses the same >= 1% rule, so it names the same entities and has no Other.
    assert [s["label"] for s in evolution["series"]] == [s["label"] for s in pie["slices"]]
    again, again_evolution = share_view(q, "2026-01-04", "2026-01-08")
    assert again["slices"] == pie["slices"] and again_evolution["series"] == evolution["series"]


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
    tracks = q.query("rankings/tracks", filters())
    assert all(r["metadata_state"] == "missing" for r in tracks["items"])
    assert sum(r["listens"] for r in tracks["items"]) == 4
    # Unnamed albums are hidden from rankings (before rank/limit); coverage still counts them.
    albums = q.query("rankings/albums", filters())
    assert albums["items"] == [] and albums["coverage"]["albums"]["total_listens"] == 4


RG = "7a29cb18-db62-4598-9b85-9d3d9eb9638b"
RG2 = "11111111-2222-4333-8444-555555555555"
RG3 = "33333333-2222-4333-8444-555555555555"
COVER = "https://coverartarchive.org/release-group/" + RG + "/front"


def add_listen(c, day, artist, album, rg=None, release=None):
    c.execute(
        text("""
      INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,release_name,
                          release_group_mbid,release_mbid)
      VALUES ('identity-test',CAST(:at AS timestamptz),CAST(:msid AS uuid),:artist,:album,
              CAST(:rg AS uuid),CAST(:release AS uuid))
    """),
        {
            "at": f"{day}T12:00:00Z",
            "msid": str(uuid.uuid4()),
            "artist": artist,
            "album": album,
            "rg": rg,
            "release": release,
        },
    )


def albums(q, start, end=None, **kw):
    r = q.query("rankings/albums", filters(start, end, **kw))
    return {i["key"]: i for i in r["items"]}, r


@pytest.fixture
def identity(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(text("DELETE FROM listens"))
        c.execute(text("DELETE FROM artwork"))
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status) "
                "VALUES ('album',CAST(:m AS uuid),'caa',:u,'resolved')"
            ),
            {"m": RG, "u": COVER},
        )
    return q, writer


def test_album_fallback_merges_into_unique_canonical_with_cover(identity):
    q, writer = identity
    with writer.begin() as c:
        for _ in range(10):  # recent: only unmapped, differently cased/width name
            add_listen(c, "2026-01-10", "Polyphia", "ＢＥ NOT AFRAID")
        for _ in range(3):  # older + recent mapped history
            add_listen(c, "2026-01-02", "polyphia", "Be Not Afraid", rg=RG)
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    # Window holds ONLY unmapped listens: identity still resolves from history (stable).
    only_recent, r = albums(q, "2026-01-10")
    assert list(only_recent) == [RG]
    assert only_recent[RG]["listens"] == 10
    assert only_recent[RG]["image_url"] == COVER
    assert only_recent[RG]["metadata_state"] == "resolved"
    assert only_recent[RG]["mbid"] == RG
    # Wider window: summed once, no double count, same key, cover kept.
    wide, r = albums(q, "2026-01-01", "2026-01-31")
    assert list(wide) == [RG] and wide[RG]["listens"] == 13
    assert wide[RG]["label"] == "Be Not Afraid"  # label from the mapped listens
    assert r["coverage"]["albums"]["included_listens"] == 13
    assert sum(i["listens"] for i in r["items"]) == 13
    # Only-mapped window unchanged.
    old, _ = albums(q, "2026-01-02")
    assert old[RG]["listens"] == 3


def test_album_fallback_ambiguous_or_distinct_ids_preserved(identity):
    q, writer = identity
    with writer.begin() as c:
        add_listen(c, "2026-01-02", "Polyphia", "Be Not Afraid", rg=RG)
        add_listen(c, "2026-01-02", "Polyphia", "Be Not Afraid", rg=RG2)  # same title, 2nd id
        add_listen(c, "2026-01-03", "Polyphia", "be not afraid")
        add_listen(c, "2026-01-03", "Polyphia", "be not afraid")
        # several ids with the same (artist, title) merge into the smallest-id one
        # fuzzy/different artist or album: no match
        add_listen(c, "2026-01-03", "Polyphia", "Be Not Afraid (Deluxe)")
        add_listen(c, "2026-01-03", "Other", "Be Not Afraid")
        add_listen(c, "2026-01-03", "Polyphia", "Be Not Afraid ")  # trims -> ambiguous group too
        # release-only id vs release group id for same names also ambiguous
        add_listen(
            c, "2026-01-04", "Solo", "Record", release="99999999-2222-4333-8444-555555555555"
        )
        add_listen(c, "2026-01-04", "Solo", "Record", rg=RG3)
        add_listen(c, "2026-01-05", "Solo", "record")
        # missing artist or album never matches
        add_listen(c, "2026-01-06", None, "Be Not Afraid")
        add_listen(c, "2026-01-06", "Polyphia", None)
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    items, r = albums(q, "2026-01-01", "2026-01-31")
    # both Polyphia ids + the 3 ID-less (incl. trimmed) listens collapse onto the smaller id
    assert items[RG2]["listens"] == 5
    assert RG not in items
    assert items[RG3]["listens"] == 3  # Solo: release id + release group id + ID-less
    assert items['name:["polyphia", "be not afraid (deluxe)"]']["listens"] == 1
    assert items['name:["other", "be not afraid"]']["listens"] == 1
    assert r["coverage"]["albums"]["total_listens"] == 12
    assert sum(i["listens"] for i in r["items"] if i["metadata_state"] != "missing") == 11


def test_album_merge_is_per_user_agnostic_and_no_raw_mutation(identity):
    q, writer = identity
    with writer.begin() as c:
        add_listen(c, "2026-01-02", "Polyphia", "Be Not Afraid", rg=RG)
        add_listen(c, "2026-01-03", "Polyphia", "Be Not Afraid")
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    albums(q, "2026-01-01", "2026-01-31")
    with writer.connect() as c:
        row = c.execute(
            text("SELECT count(*) FILTER (WHERE release_group_mbid IS NULL) FROM listens")
        ).scalar_one()
    assert row == 1  # raw history untouched


def test_album_identity_timezone_window_madrid(identity):
    q, writer = identity
    with writer.begin() as c:
        # 2026-01-09T23:30Z is 2026-01-10 00:30 in Europe/Madrid; mapped history is outside.
        c.execute(
            text("""
          INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,release_name)
          VALUES ('identity-test','2026-01-09T23:30:00Z',CAST(:m AS uuid),'Polyphia',
                  'Be Not Afraid')"""),
            {"m": str(uuid.uuid4())},
        )
        add_listen(c, "2025-06-01", "Polyphia", "Be Not Afraid", rg=RG)
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    madrid, _ = albums(q, "2026-01-10", tz="Europe/Madrid")
    assert madrid[RG]["listens"] == 1
    utc, _ = albums(q, "2026-01-10", tz="UTC")
    assert utc == {}


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


def seed_multi_genre(writer):
    """Own listens/tags. Returns genre ids. 5 listens, 4 covered, 8 genre appearances."""
    ids = {k: str(uuid.uuid4()) for k in ("rock", "jazz", "metal", "rec", "rec2", "rg", "a1", "a2")}
    with writer.begin() as c:
        c.execute(text("DELETE FROM entity_genres"))
        c.execute(text("DELETE FROM listens"))
        for key, name in (("rock", "Rock"), ("jazz", "Jazz"), ("metal", "Metal")):
            c.execute(
                text("INSERT INTO genres(genre_mbid,name) VALUES (CAST(:i AS uuid),:n)"),
                {"i": ids[key], "n": name},
            )

        def tag(kind, entity, genre, votes):
            c.execute(
                text(
                    "INSERT INTO entity_genres(entity_type,entity_mbid,genre_mbid,votes) "
                    "VALUES (:k,CAST(:e AS uuid),CAST(:g AS uuid),:v)"
                ),
                {"k": kind, "e": ids[entity], "g": ids[genre], "v": votes},
            )

        tag("recording", "rec", "rock", 50)  # Rock + zero-vote Jazz on the recording
        tag("recording", "rec", "jazz", 0)
        tag("release_group", "rg", "metal", 9)  # shadowed by recording; never counted for rec
        tag("release_group", "rg", "rock", -1)  # rg-level fallback with a negative-vote tag
        tag("artist", "a1", "rock", 3)  # two credited artists share Rock, one adds Jazz
        tag("artist", "a2", "rock", 1)
        tag("artist", "a2", "jazz", 1)

        def listen(n, **cols):
            row = c.execute(
                text(
                    "INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,"
                    "recording_mbid,release_group_mbid) VALUES ('multi-genre',"
                    "timestamptz '2026-01-05T12:00Z'+make_interval(mins=>:n),"
                    "gen_random_uuid(),'Artist',CAST(:r AS uuid),CAST(:g AS uuid)) RETURNING id"
                ),
                {"n": n, "r": cols.get("rec"), "g": cols.get("rg")},
            ).scalar_one()
            for position, artist in enumerate(cols.get("artists", ())):
                c.execute(
                    text(
                        "INSERT INTO listen_artists(listen_id,position,artist_name,artist_mbid) "
                        "VALUES (:l,:p,:n,CAST(:a AS uuid))"
                    ),
                    {"l": row, "p": position, "n": artist, "a": ids[artist]},
                )

        listen(1, rec=ids["rec"], rg=ids["rg"])  # Rock + Jazz
        listen(2, rec=ids["rec"], rg=ids["rg"])  # repeated play: counted again
        listen(3, artists=("a1", "a2", "a1"))  # artist level: Rock once, Jazz once
        listen(4, rec=ids["rec2"])  # no tags anywhere: unknown, omitted
        listen(5, rg=ids["rg"])  # release-group level: Metal + negative-vote Rock
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    return ids


def weighted(q, family):
    data = q.query(family, filters())
    if family.startswith("pies"):
        return {r["label"]: r["listens"] for r in data["slices"]}, data
    return {r["label"]: sum(r["values"]) for r in data["series"]}, data


def test_genre_ranking_counts_distinct_genres_but_charts_stay_vote_weighted(stats):
    q, writer = stats
    seed_multi_genre(writer)
    ranking = q.query("rankings/genres", filters(limit=100))
    RankingResponse.model_validate(ranking)
    # Rock: listens 1,2,3 + rg listen 5 (-1 vote). Jazz: 1,2 (zero vote!),3. Metal: listen 5.
    assert {r["label"]: r["listens"] for r in ranking["items"]} == {
        "Rock": 4,
        "Jazz": 3,
        "Metal": 1,
    }
    # Charts: votes weight, nonpositive tags are dropped when positives exist (so the
    # zero-vote Jazz on listens 1-2 and the -1 Rock on listen 5 contribute nothing),
    # artist level 1/2 per artist. Totals equal the 4 covered listens.
    for family in ("pies/genres", "evolution/genres"):
        values, data = weighted(q, family)
        assert values == pytest.approx({"Rock": 2.75, "Jazz": 0.25, "Metal": 1})
        assert sum(values.values()) == pytest.approx(data["coverage"]["genres"]["included_listens"])
    pie = q.query("pies/genres", filters())
    assert {s["label"]: s["share"] for s in pie["slices"]} == pytest.approx(
        {"Rock": 2.75 / 4, "Jazz": 0.25 / 4, "Metal": 1 / 4}
    )


def test_genre_ranking_coverage_and_share_conservation(stats):
    q, writer = stats
    seed_multi_genre(writer)
    data = q.query("rankings/genres", filters(limit=100))
    assert_coverage(data)
    assert data["coverage"]["genres"] == {
        "total_listens": 5,
        "included_listens": 4,
        "excluded_listens": 1,
    }
    assert sum(r["listens"] for r in data["items"]) == 8  # appearances exceed covered listens
    assert {r["label"]: r["share"] for r in data["items"]} == pytest.approx(
        {"Rock": 4 / 8, "Jazz": 3 / 8, "Metal": 1 / 8}
    )
    assert sum(r["share"] for r in data["items"]) == pytest.approx(1)
    # Other dimensions and chart coverage are untouched.
    assert data["coverage"]["artists"]["included_listens"] == 5
    assert q.query("pies/genres", filters())["coverage"]["genres"] == data["coverage"]["genres"]


def test_genre_ranking_limit_and_duplicate_tags(stats):
    q, writer = stats
    seed_multi_genre(writer)
    top = q.query("rankings/genres", filters(limit=2))
    assert [(r["label"], r["listens"]) for r in top["items"]] == [("Rock", 4), ("Jazz", 3)]
    # A duplicated credit and two artists tagging the same genre add Rock once for listen 3
    # (Rock total already proven 4 above); repeat plays of the same listen count separately.
    with writer.begin() as c:
        c.execute(
            text("""
          INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,recording_mbid)
          SELECT 'multi-genre',timestamptz '2026-01-05T14:00Z',gen_random_uuid(),'Artist',
                 recording_mbid FROM listens WHERE user_name='multi-genre'
                 AND recording_mbid IS NOT NULL AND release_group_mbid IS NOT NULL LIMIT 1
        """)
        )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    again = q.query("rankings/genres", filters(limit=100))
    assert {r["label"]: r["listens"] for r in again["items"]} == {"Rock": 5, "Jazz": 4, "Metal": 1}


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


def test_feat_listen_merges_into_the_id_album_via_primary_credit(identity):
    q, writer = identity
    with writer.begin() as c:
        add_listen(c, "2026-01-02", "YUJU", "[REC.]", rg=RG)
        add_listen(c, "2026-01-03", "YUJU feat. Mad Clown", "[REC.]")  # raw artist differs
        add_listen(c, "2026-01-03", "YUJU feat. Mad Clown", "[REC.]")
        c.execute(
            text(
                "INSERT INTO listen_artists(listen_id,position,artist_name) "
                "SELECT id,0,'YUJU' FROM listens WHERE artist_name LIKE 'YUJU feat.%'"
            )
        )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    items, _ = albums(q, "2026-01-01", "2026-01-31")
    assert {k: v["listens"] for k, v in items.items()} == {RG: 3}


def test_localized_credit_name_does_not_split_the_album(identity):
    """Mapped listen credits '유주' (raw artist 'YUJU'); a name-only 'YUJU' listen still joins."""
    q, writer = identity
    with writer.begin() as c:
        add_listen(c, "2026-01-02", "YUJU", "In Bloom", rg=RG)
        add_listen(c, "2026-01-03", "YUJU", "In Bloom")
        c.execute(
            text(
                "INSERT INTO listen_artists(listen_id,position,artist_name) "
                "SELECT id,0,'\uc720\uc8fc' FROM listens WHERE release_group_mbid IS NOT NULL"
            )
        )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    items, _ = albums(q, "2026-01-01", "2026-01-31")
    assert {k: v["listens"] for k, v in items.items()} == {RG: 2}


def test_summary_totals_are_uncapped_and_not_inflated_by_credits(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(
            text("""
          INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,track_name)
          SELECT 'summary-wide',timestamptz '2026-03-05T12:00Z',
                 md5('summary-'||g)::uuid,'Summary artist '||g,'Summary track '||g
          FROM generate_series(1,25) g
        """)
        )
    base = q.query("summary", filters("2026-03-05"))
    SummaryResponse.model_validate(base)
    assert (base["plays"], base["tracks"], base["artists"]) == (25, 25, 25)
    assert base["plays"] == base["coverage"]["listens"]["total_listens"]
    seeded = q.query("summary", filters("2026-01-05"))
    listens = seeded["coverage"]["listens"]["total_listens"]
    assert seeded["plays"] == listens
    empty = q.query("summary", filters("2030-01-01"))
    assert (empty["plays"], empty["tracks"], empty["artists"], empty["minutes"]) == (0, 0, 0, 0)


def test_summary_multi_credit_listen_counts_once(stats):
    q, writer = stats
    with writer.begin() as c:
        c.execute(
            text("""
          WITH l AS (
            INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,track_name)
            VALUES ('summary-credits',timestamptz '2026-04-05T12:00Z',
                    md5('summary-credits')::uuid,'Duo A & Duo B','Duo song')
            RETURNING id)
          INSERT INTO listen_artists(listen_id,position,artist_name)
          SELECT id,0,'Duo A' FROM l UNION ALL SELECT id,1,'Duo B' FROM l
        """)
        )
    data = q.query("summary", filters("2026-04-05"))
    assert (data["plays"], data["tracks"], data["artists"]) == (1, 1, 2)
