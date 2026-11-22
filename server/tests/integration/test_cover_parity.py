"""One shared artwork resolver: same item, same cover in rankings, history feed and artist page."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.application import artist_history as ah
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.public_history_queries import SQLPublicHistory
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

pytestmark = pytest.mark.integration

ARTIST, RG, REC = (str(uuid.uuid4()) for _ in range(3))
ALBUM_URL = f"https://coverartarchive.org/release-group/{RG}/front-250"
NAME_URL = "https://cdn-images.dzcdn.net/images/cover/" + "e" * 32 + "/loose.jpg"
ARTIST_URL = "https://cdn-images.dzcdn.net/images/artist/" + "d" * 32 + "/photo.jpg"


def _listen(c, track, album, rg=None, rec=None, artist=("Ann", ARTIST)):
    lid = c.execute(
        text("""INSERT INTO listens(user_name,listened_at,recording_msid,track_name,artist_name,
                release_name,release_group_mbid,recording_mbid)
                VALUES ('t',CAST('2026-01-05T12:00:00Z' AS timestamptz),CAST(:m AS uuid),:t,:an,:al,
                CAST(:rg AS uuid),CAST(:rec AS uuid)) RETURNING id"""),
        {"m": str(uuid.uuid4()), "t": track, "an": artist[0], "al": album, "rg": rg, "rec": rec},
    ).scalar_one()
    c.execute(
        text("INSERT INTO listen_artists VALUES (:l,0,:n,CAST(:m AS uuid))"),
        {"l": lid, "n": artist[0], "m": artist[1]},
    )


def test_cover_parity(migrated_db, api_url, monkeypatch):
    with migrated_db.begin() as c:
        c.execute(text("DELETE FROM listens"))
        c.execute(text("DELETE FROM artwork"))
        c.execute(text("DELETE FROM artwork_name"))
        _listen(c, "Mapped", "Mapped Album", rg=RG, rec=REC)  # album cover only
        _listen(c, "Loose", "Loose Album")  # name-keyed, album cover by name
        _listen(c, "Bare", "Bare Album", artist=("Zed", None))  # nothing resolvable
        c.execute(
            text("""INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status)
                    VALUES ('album',CAST(:rg AS uuid),'caa',:u,'resolved'),
                           ('artist',CAST(:a AS uuid),'deezer',:p,'resolved')"""),
            {"rg": RG, "a": ARTIST, "u": ALBUM_URL, "p": ARTIST_URL},
        )
        c.execute(
            text("""INSERT INTO artwork_name(entity_type,name_key,source,image_url,status,
                    match_kind,provider_ref)
                    VALUES ('album','name:["ann", "loose album"]','deezer',:u,'resolved',
                    'album_title_artist','1')"""),
            {"u": NAME_URL},
        )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    engine = create_engine(api_url)
    monkeypatch.setattr(ah.settings, "fixture_mode", False)
    monkeypatch.setattr(ah.settings, "public_event_feeds_enabled", True, raising=False)
    try:
        flt = StatisticsFilter(date(2026, 1, 5), date(2026, 1, 5), "UTC", 10)
        stats = SQLStatisticsQueries(engine)
        albums = {i["key"]: i["image_url"] for i in stats.query("rankings/albums", flt)["items"]}
        artists = {i["key"]: i["image_url"] for i in stats.query("rankings/artists", flt)["items"]}
        assert albums[RG] == ALBUM_URL
        assert artists[ARTIST] == ARTIST_URL

        feed = {i.title: i.image_url for i in SQLPublicHistory(engine).fetch("UTC", 10).items}
        page = ah.ArtistHistory(engine).recent(ARTIST, limit=10)
        # Rankings, feed and artist recent agree; album cover wins.
        assert feed["Mapped"] == albums[RG]
        recent = {i.title: i.image_url for i in page.items}
        assert recent["Mapped"] == albums[RG]
        assert recent["Loose"] == NAME_URL
        assert feed["Loose"] == NAME_URL
        assert feed["Bare"] is None
        # Artist endpoints use the same resolver as the artist ranking.
        assert ah.ArtistHistory(engine).summary(ARTIST).image_url == artists[ARTIST]
        top = ah.ArtistHistory(engine).rankings(ARTIST, "albums")
        assert {i.key: i.image_url for i in top.items}[RG] == albums[RG]
    finally:
        engine.dispose()
