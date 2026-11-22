"""Artist-history lane: counts/credits, paging, heatmap year, recent privacy, credits resolver."""

import re
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from music_dashboard.application import artist_history as mod
from music_dashboard.application.artist_history import ArtistHistory

pytestmark = pytest.mark.integration

A, B = str(uuid.uuid4()), str(uuid.uuid4())
RG, REC = str(uuid.uuid4()), str(uuid.uuid4())


def add(c, at, track, artists, album="Album", rg=None, rec=None):
    lid = c.execute(
        text("""INSERT INTO listens(user_name,listened_at,recording_msid,track_name,artist_name,
                 release_name,release_group_mbid,recording_mbid)
                VALUES ('t',CAST(:at AS timestamptz),CAST(:m AS uuid),:t,:an,:al,
                 CAST(:rg AS uuid),CAST(:rec AS uuid)) RETURNING id"""),
        {
            "at": at,
            "m": str(uuid.uuid4()),
            "t": track,
            "an": " & ".join(n for n, _ in artists),
            "al": album,
            "rg": rg,
            "rec": rec,
        },
    ).scalar_one()
    for i, (name, mbid) in enumerate(artists):
        c.execute(
            text("INSERT INTO listen_artists VALUES (:l,:p,:n,CAST(:m AS uuid))"),
            {"l": lid, "p": i, "n": name, "m": mbid},
        )


@pytest.fixture
def hist(migrated_db, api_url, monkeypatch):
    with migrated_db.begin() as c:
        # Collab: one play for each credited artist, counted ONCE per artist.
        add(c, "2025-12-31T23:30:00Z", "Duet", [("Ann", A), ("Ann", A), ("Bob", B)], rg=RG, rec=REC)
        add(c, "2026-01-01T10:00:00Z", "Duet", [("Ann", A), ("Bob", B)], rg=RG, rec=REC)
        add(c, "2026-01-01T11:00:00Z", "Solo", [("Ann", A)], album="Other")
        add(c, "2026-01-01T12:00:00Z", "Solo", [("Ann", A)], album="Other")
        add(c, "2026-01-02T12:00:00Z", "Named", [("Ça Va", None)], album="N")
    engine = create_engine(api_url)
    monkeypatch.setattr(mod.settings, "fixture_mode", False)
    monkeypatch.setattr(mod.settings, "public_event_feeds_enabled", True, raising=False)
    yield ArtistHistory(engine)
    engine.dispose()


def test_summary_counts_one_play_per_artist_per_listen(hist):
    assert hist.summary(A).total_listens == 4
    assert hist.summary(B).total_listens == 2
    assert hist.summary("name:ça va").total_listens == 1
    with pytest.raises(HTTPException) as e:
        hist.summary(str(uuid.uuid4()))
    assert e.value.status_code == 404


def test_rankings_page_over_all_items_with_opaque_cursor(hist):
    first = hist.rankings(A, "tracks", limit=1)
    assert [i.listens for i in first.items] == [2] and first.next_cursor
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", first.next_cursor)
    second = hist.rankings(A, "tracks", limit=1, cursor=first.next_cursor)
    assert second.items[0].key != first.items[0].key
    assert second.next_cursor is None
    with pytest.raises(HTTPException) as e:  # cursor for another query
        hist.rankings(B, "tracks", limit=1, cursor=first.next_cursor)
    assert e.value.status_code == 409
    albums = hist.rankings(A, "albums", limit=10)
    assert sorted(i.listens for i in albums.items) == [2, 2]


def test_heatmap_year_and_timezone(hist):
    utc = hist.heatmap(A, year=2026)
    assert utc.years == [2025, 2026] and utc.year == 2026
    assert {str(x.day): x.listens for x in utc.cells} == {"2026-01-01": 3}
    # Chicago: the 23:30Z listen is Dec 31 local, still 2025; plays shift days.
    ny = hist.heatmap(A, year=2025, tz="America/New_York")
    assert [str(x.day) for x in ny.cells] == ["2025-12-31"]
    with pytest.raises(HTTPException):
        hist.heatmap(A, tz="Nope/Zone")


def test_recent_has_no_times_and_pages_with_ties(hist):
    page = hist.recent(A, limit=3)
    body = page.model_dump_json()
    assert "2026" not in body and "listened" not in body and 'id"' not in body
    assert [i.title for i in page.items] == ["Solo", "Solo", "Duet"]
    rest = hist.recent(A, limit=3, cursor=page.next_cursor)
    assert [i.title for i in rest.items] == ["Duet"] and rest.next_cursor is None


def test_recent_fails_closed_when_disabled(hist, monkeypatch):
    monkeypatch.setattr(mod.settings, "public_event_feeds_enabled", False, raising=False)
    with pytest.raises(HTTPException) as e:
        hist.recent(A)
    assert e.value.status_code == 503


def test_credits_resolver_only_reliable(hist):
    got = hist.credits("tracks", REC)["artists"]
    assert {a["key"] for a in got} == {A, B}  # collab: individual credits, no guessing
    assert hist.credits("albums", RG)["artists"] == got
    assert hist.credits("tracks", str(uuid.uuid4()))["artists"] == []
    assert hist.credits("albums", "name:x")["artists"] == []
