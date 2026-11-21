"""Artist photo -> album cover -> initials fallback in rankings and country artist lists."""

import uuid
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

pytestmark = pytest.mark.integration

H = "e" * 32
PHOTO = f"https://cdn-images.dzcdn.net/images/artist/{H}/1000x1000-000000-80-0-0.jpg"


def cover(name):
    return f"https://cdn-images.dzcdn.net/images/cover/{H}/{name}.jpg"


def u(n):
    return f"00000000-0000-4000-8000-{n:012d}"


PIA, QUIN, RAE, SAM, TOM, SAME_V, SAME_W, NOMAD_ID = (u(n) for n in range(500, 508))
ALB_PIA, ALB_Q1, ALB_Q2, ALB_Q_NOCOVER = u(600), u(601), u(602), u(603)
ALB_R2, ALB_R1, ALB_COMP, ALB_W, ALB_NOCOVER_ONLY = u(612), u(611), u(620), u(630), u(640)
LOSER = u(508)  # only albums without any cover


class Seq:
    n = 0


def listen(c, artist, mbid, *, rg=None, album=None, extra=()):
    Seq.n += 1
    row = c.execute(
        text(
            "INSERT INTO listens(user_name,listened_at,recording_msid,track_name,artist_name,"
            "release_name,release_group_mbid) VALUES ('fb', timestamptz '2026-03-01T10:00Z' "
            "+ make_interval(mins=>:n), :msid, 'T', :artist, :album, CAST(:rg AS uuid)) "
            "RETURNING id"
        ),
        {"n": Seq.n, "msid": str(uuid.uuid4()), "artist": artist, "album": album, "rg": rg},
    ).scalar_one()
    credits = [(artist, mbid), *extra] if mbid or extra else []
    for pos, (name, m) in enumerate(credits):
        c.execute(
            text(
                "INSERT INTO listen_artists(listen_id,position,artist_name,artist_mbid) "
                "VALUES (:i,:p,:n,CAST(:m AS uuid))"
            ),
            {"i": row, "p": pos, "n": name, "m": m},
        )


def album_cover(c, rg, name):
    c.execute(
        text(
            "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status,match_kind) "
            "VALUES ('album',CAST(:k AS uuid),'caa',:u,'resolved','id')"
        ),
        {"k": rg, "u": cover(name)},
    )


@pytest.fixture
def env(migrated_db, api_url):
    Seq.n = 0
    with migrated_db.begin() as c:
        for _ in range(9):  # Pia: photo AND album cover, photo wins
            listen(c, "Pia", PIA, rg=ALB_PIA, album="Pia LP")
        for _ in range(2):
            listen(c, "Quin", QUIN, rg=ALB_Q1, album="Q1")
        for _ in range(3):  # most played covered album
            listen(c, "Quin", QUIN, rg=ALB_Q2, album="Q2")
        for _ in range(4):  # even more played, but no cover: skipped
            listen(c, "Quin", QUIN, rg=ALB_Q_NOCOVER, album="Q3")
        for _ in range(2):  # tie between two covered albums: smallest key
            listen(c, "Rae", RAE, rg=ALB_R2, album="R2")
            listen(c, "Rae", RAE, rg=ALB_R1, album="R1")
        # compilation: three artists, one listen each -> nobody owns it
        listen(c, "Sam", SAM, rg=ALB_COMP, album="Hits", extra=())
        listen(c, "Tom", TOM, rg=ALB_COMP, album="Hits")
        listen(c, "Various Artists", u(999), rg=ALB_COMP, album="Hits")
        # homonyms: same name, different MBIDs; only W has an album cover
        listen(c, "Same", SAME_V)
        listen(c, "Same", SAME_W, rg=ALB_W, album="W LP")
        listen(c, "Same", SAME_W, rg=ALB_W, album="W LP")
        # name-only artist (no credit rows) with a name-only album that has a cover
        for _ in range(2):
            listen(c, "Nomad", None, album="Dunes")
        listen(c, "Nomad", NOMAD_ID)  # a DIFFERENT, MBID-identified "Nomad" with no album
        listen(c, "Loser", LOSER, rg=ALB_NOCOVER_ONLY, album="No art")
        for rg, name in (
            (ALB_PIA, "pia"),
            (ALB_Q1, "q1"),
            (ALB_Q2, "q2"),
            (ALB_R1, "r1"),
            (ALB_R2, "r2"),
            (ALB_COMP, "hits"),
            (ALB_W, "w"),
        ):
            album_cover(c, rg, name)
        c.execute(
            text(
                "INSERT INTO artwork_name(entity_type,name_key,source,image_url,status,"
                "match_kind,provider_ref) VALUES ('album','name:[\"nomad\", \"dunes\"]',"
                "'deezer',:u,'resolved','album_title_artist','1')"
            ),
            {"u": cover("dunes")},
        )
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status,match_kind)"
                " VALUES ('artist',CAST(:k AS uuid),'deezer',:u,'resolved','id')"
            ),
            {"k": PIA, "u": PHOTO},
        )
        for m in (QUIN, RAE, SAM, TOM):
            c.execute(
                text("INSERT INTO artist_countries(artist_mbid,country_code) VALUES (:m,'US')"),
                {"m": m},
            )
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    api = create_engine(api_url)
    yield migrated_db, api
    api.dispose()


def flt(country=None):
    return StatisticsFilter(date(2026, 1, 1), date(2026, 12, 31), "UTC", 100, country)


def images(result):
    return {i["key"]: i["image_url"] for i in result["items"]}


def test_precedence_matching_and_missing_cover(env):
    admin, api = env
    got = images(SQLStatisticsQueries(api).query("rankings/artists", flt()))
    assert got[PIA] == PHOTO  # resolved photo beats its own album cover
    assert got[QUIN] == cover("q2")  # most-played album WITH a cover (q3 has none)
    assert got[RAE] == cover("r1")  # tie -> smallest album key
    assert got[SAM] is None and got[TOM] is None  # unrelated compilation
    assert got[SAME_V] is None  # homonym does not borrow SAME_W's album
    assert got[SAME_W] == cover("w")
    assert got["name:nomad"] == cover("dunes")  # safe name-only case
    assert got[NOMAD_ID] is None  # MBID "Nomad" never matches the name-only artist's album
    assert got[LOSER] is None  # no cover anywhere -> client shows initials
    with admin.connect() as c:  # never persisted as an artist photo
        rows = c.execute(
            text("SELECT entity_mbid::text, image_url FROM artwork WHERE entity_type='artist'")
        ).all()
    assert rows == [(PIA, PHOTO)]
    with admin.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM artwork_name WHERE entity_type='artist'")
            ).scalar_one()
            == 0
        )


def test_invalid_cover_url_is_skipped(env):
    admin, api = env
    with admin.begin() as c:  # https but not allowlisted: DB accepts, response must not
        c.execute(
            text(
                "UPDATE artwork SET image_url='https://evil-archive.org/x.jpg' "
                "WHERE entity_mbid=CAST(:k AS uuid) AND entity_type='album'"
            ),
            {"k": ALB_Q2},
        )
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    got = images(SQLStatisticsQueries(api).query("rankings/artists", flt()))
    assert got[QUIN] == cover("q1")  # next eligible validated cover


def test_country_artists_use_fallback_and_totals_and_order_are_stable(env):
    admin, api = env
    q = SQLStatisticsQueries(api)
    country = q.query("countries/artists", flt("US"))
    imgs = images(country)
    assert imgs[QUIN] == cover("q2") and imgs[RAE] == cover("r1")
    assert imgs[SAM] is None and imgs[TOM] is None
    with admin.begin() as c:  # remove every album cover: only images change
        c.execute(text("DELETE FROM artwork WHERE entity_type='album'"))
        c.execute(text("UPDATE data_generation SET enrichment_generation=enrichment_generation+1"))
    bare = q.query("countries/artists", flt("US"))
    shape = lambda r: [(i["key"], i["label"], i["listens"], i["share"]) for i in r["items"]]  # noqa: E731
    assert shape(bare) == shape(country)
    assert bare["coverage"] == country["coverage"]
    assert all(i["image_url"] is None for i in bare["items"])
    rankings = q.query("rankings/artists", replace(flt()))
    assert rankings["coverage"]["artists"] == bare["coverage"]["artists"]
    assert [i["listens"] for i in rankings["items"]] == sorted(
        (i["listens"] for i in rankings["items"]), reverse=True
    )
