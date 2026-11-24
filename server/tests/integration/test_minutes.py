"""Minutes metric: track lengths, average fallback and full length per credited artist."""

import uuid
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

pytestmark = pytest.mark.integration

R3MIN, R1MIN, UNKNOWN = (str(uuid.uuid4()) for _ in range(3))


@pytest.fixture
def minutes_db(migrated_db, api_url):
    with migrated_db.begin() as c:
        c.execute(
            text(
                "INSERT INTO recording_durations VALUES (CAST(:a AS uuid), 180000),"
                " (CAST(:b AS uuid), 60000)"
            ),
            {"a": R3MIN, "b": R1MIN},
        )

        def listen(recording, credits):
            listen_id = c.execute(
                text(
                    "INSERT INTO listens(user_name,listened_at,recording_msid,recording_mbid,"
                    "track_name,artist_name) VALUES ('m',timestamptz '2026-02-01T12:00Z',"
                    "gen_random_uuid(),CAST(:r AS uuid),:t,:a) RETURNING id"
                ),
                {"r": recording, "t": f"t-{recording[:4]}", "a": credits[0]},
            ).scalar_one()
            for position, name in enumerate(credits):
                c.execute(
                    text("INSERT INTO listen_artists VALUES (:i,:p,:n,NULL)"),
                    {"i": listen_id, "p": position, "n": name},
                )

        listen(R3MIN, ["Alpha"])
        listen(R1MIN, ["Alpha"])
        listen(UNKNOWN, ["Beta"])  # average known length: (3 + 1) / 2 = 2 minutes
        listen(R3MIN, ["Alpha", "Beta"])  # every credit gets the full 3 minutes
    engine = create_engine(api_url)
    yield SQLStatisticsQueries(engine)
    engine.dispose()


def base(metric):
    return StatisticsFilter(date(2026, 2, 1), date(2026, 2, 1), "UTC", 10, metric=metric)


def totals(q, family, metric):
    return {i["label"]: i["listens"] for i in q.query(family, base(metric))["items"]}


def test_artist_ranking_minutes_use_full_length_and_average_fallback(minutes_db):
    assert totals(minutes_db, "rankings/artists", "listens") == {"Alpha": 2.5, "Beta": 1.5}
    assert totals(minutes_db, "rankings/artists", "minutes") == {"Alpha": 7.0, "Beta": 5.0}


def test_track_ranking_and_heatmap_minutes(minutes_db):
    tracks = totals(minutes_db, "rankings/tracks", "minutes")
    assert tracks[f"t-{R3MIN[:4]}"] == 6.0
    assert tracks[f"t-{UNKNOWN[:4]}"] == 2.0
    cells = minutes_db.query("heatmap", base("minutes"))["cells"]
    assert [c["listens"] for c in cells] == [9]  # 3 + 1 + 2 + 3
    assert [c["listens"] for c in minutes_db.query("heatmap", base("listens"))["cells"]] == [4]


def test_minutes_ignored_by_pies_and_cached_separately(minutes_db):
    pie = minutes_db.query("pies/artists", base("minutes"))
    same = minutes_db.query("pies/artists", replace(base("minutes"), metric="listens"))
    assert pie["slices"] == same["slices"]
    first = minutes_db.query("rankings/artists", base("minutes"))
    assert first["items"] != minutes_db.query("rankings/artists", base("listens"))["items"]
