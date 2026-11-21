import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from test_artwork_adapters import COVER, MBID, PIC, UA, Fake, NoLimit, js

from music_dashboard.application.jobs import artwork as job
from music_dashboard.application.resolve_artwork import QuotaGate, ResolveArtwork
from music_dashboard.domain.artwork.entities import ArtworkOutcome, ArtworkWork
from music_dashboard.domain.artwork.errors import ArtworkQuotaError, ArtworkTransientError
from music_dashboard.domain.artwork.policies import ArtworkPolicy
from music_dashboard.infrastructure.artwork.caa import CoverArtArchiveClient
from music_dashboard.infrastructure.artwork.deezer import DeezerClient
from music_dashboard.infrastructure.artwork.wikidata import WikidataClient
from music_dashboard.infrastructure.musicbrainz.artwork_lookup import MusicBrainzArtworkLookups
from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
from music_dashboard.infrastructure.musicbrainz.transport import HttpResponse
from music_dashboard.presentation.cli_commands.artwork import configure, run

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)
REL = "22222222-2222-4222-8222-222222222222"


@dataclass
class Rel:
    barcode: str | None = None
    deezer_album_id: str | None = None
    release_group_mbid: str | None = None


@dataclass
class Rec:
    isrcs: list
    release_mbids: list


@dataclass
class Links:
    deezer_artist_id: str | None = None
    wikidata_qid: str | None = None
    name: str | None = None
    aliases: tuple = ()


class MB:
    def __init__(self, release=None, isrcs=(), links=None, error=None, releases=()):
        self._release, self._isrcs, self._links, self.error = release, list(isrcs), links, error
        self._releases = list(releases)
        self.calls = []

    def release(self, mbid):
        self.calls.append(("release", mbid))
        if self.error:
            raise self.error
        return self._release

    def recording(self, mbid):
        self.calls.append(("recording", mbid))
        return Rec(self._isrcs, self._releases)

    def artist_links(self, mbid):
        self.calls.append(("artist", mbid))
        return self._links or Links()


class Dz:
    def __init__(self, upc=None, album=None, isrc=None, artist=None, error=None):
        self.map = {"upc": upc, "album": album, "isrc": isrc, "artist": artist}
        self.error, self.calls = error, []

    def _hit(self, kind, key):
        self.calls.append((kind, key))
        if self.error:
            raise self.error
        return self.map[kind]

    def album_by_upc(self, b):
        return self._hit("upc", b)

    def album(self, i):
        return self._hit("album", i)

    def track_album_cover(self, i):
        return self._hit("isrc", i)

    def artist_picture(self, i):
        return self._hit("artist", i)

    # name search (fallback): scripted per query; every call is recorded
    searches: dict = {}

    def _search(self, *query):
        self.calls.append(("search", *query))
        if self.error:
            raise self.error
        return self.searches.get(query, [])

    def search_artists(self, name):
        return self._search("artist", name)

    def search_albums(self, title, artist=None):
        return self._search("album", title, artist)

    def search_tracks(self, title, artist):
        return self._search("track", title, artist)


class Caa:
    def __init__(self, hits=(), hints=()):
        self.hits, self.hints, self.calls = set(hits), set(hints), []

    def hint_url(self, release_mbid, caa_id):
        self.calls.append(("hint", release_mbid, caa_id))
        return (
            f"https://coverartarchive.org/release/{release_mbid}/{caa_id}-500.jpg"
            if (release_mbid, caa_id) in self.hints
            else None
        )

    def front_url(self, kind, mbid):
        self.calls.append((kind, mbid))
        return (
            f"https://coverartarchive.org/{kind}/{mbid}/front"
            if (kind, mbid) in self.hits
            else None
        )


class Wd:
    def __init__(self, value=None):
        self.value, self.calls = value, []

    def deezer_artist_id(self, qid):
        self.calls.append(qid)
        return self.value


class Repo:
    def __init__(self, work):
        self.work, self.saved, self.bumped, self._attempts = work, [], 0, 0

    def due(self, now, limit):
        return self.work[:limit]

    def attempts(self, work):
        return self._attempts

    def save(self, work, outcome, now, next_retry_at):
        self.saved.append((work, outcome, next_retry_at - now))
        return outcome.status == "resolved"

    def bump_generation(self):
        self.bumped += 1


def resolver(work, mb=None, dz=None, caa=None, wd=None, quota=None):
    repo = Repo(work)
    r = ResolveArtwork(
        repo, mb or MB(), dz or Dz(), caa or Caa(), wd or Wd(),
        ArtworkPolicy(), clock=lambda: NOW, rand=lambda: 0.5, quota=quota,
    )  # fmt: skip
    return r, repo


ALBUM = ArtworkWork("album", MBID, REL, 5)
TRACK = ArtworkWork("track", MBID, REL, 5)
ARTIST = ArtworkWork("artist", MBID, None, 5)


def outcome(repo):
    return repo.saved[0][1]


def test_album_prefers_deezer_upc_then_url_rel_then_caa():
    mb, dz, caa = MB(Rel("0123456789012", "55")), Dz(upc=COVER), Caa({("release-group", MBID)})
    r, repo = resolver([ALBUM], mb, dz, caa)
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", COVER)
    assert caa.calls == []
    # UPC miss -> url-rel album id
    dz = Dz(upc=None, album=COVER)
    r, repo = resolver([ALBUM], MB(Rel("0123456789012", "55")), dz, caa)
    r.run()
    assert dz.calls == [("upc", "0123456789012"), ("album", "55")]
    assert outcome(repo).source == "deezer"
    # no barcode, no rel -> CAA release-group, original coverartarchive URL
    r, repo = resolver([ALBUM], MB(Rel()), Dz(), Caa({("release-group", MBID)}))
    r.run()
    assert outcome(repo) == ArtworkOutcome(
        "resolved", "caa", f"https://coverartarchive.org/release-group/{MBID}/front"
    )
    # nothing anywhere -> miss; no release known -> straight to CAA
    mb = MB(None)
    r, repo = resolver([ArtworkWork("album", MBID, None, 1)], mb, Dz(), Caa())
    r.run()
    assert outcome(repo).status == "miss" and mb.calls == []


def test_track_caa_own_release_first_then_isrc_then_miss():
    caa = Caa({("release", REL)})
    mb = MB(isrcs=["USABC1234567"])
    r, repo = resolver([TRACK], mb, Dz(isrc=COVER), caa)
    r.run()
    assert outcome(repo).source == "caa" and mb.calls == []  # no MB call needed
    dz = Dz(isrc=COVER)
    r, repo = resolver([TRACK], MB(isrcs=["USABC1234567"]), dz, Caa())
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", COVER)
    assert dz.calls == [("isrc", "USABC1234567")]
    r, repo = resolver([TRACK], MB(isrcs=[]), Dz(), Caa())
    r.run()
    assert outcome(repo).status == "miss"


HINT = ("33333333-3333-4333-8333-333333333333", "123456")
HINT_URL = f"https://coverartarchive.org/release/{HINT[0]}/{HINT[1]}-500.jpg"


def test_lb_caa_hint_wins_without_musicbrainz_or_deezer_calls():
    for kind in ("album", "track"):
        mb, dz = MB(Rel("0123456789012")), Dz(upc=COVER, isrc=COVER)
        work = ArtworkWork(kind, MBID, REL, 5, caa_hint=HINT)  # type: ignore[arg-type]
        r, repo = resolver([work], mb, dz, Caa(hints={HINT}))
        r.run()
        assert outcome(repo) == ArtworkOutcome("resolved", "caa", HINT_URL)
        assert mb.calls == [] and dz.calls == []
    # a stale hint (CAA 404) falls through to the normal chain
    r, repo = resolver([ArtworkWork("album", MBID, REL, 5, caa_hint=HINT)], MB(Rel()), Dz(), Caa())
    r.run()
    assert outcome(repo).status == "miss"


def test_release_keyed_album_uses_release_front_then_mb_release_group():
    work = ArtworkWork("album", REL, REL, 3, album_kind="release")
    caa = Caa({("release", REL)})
    r, repo = resolver([work], MB(Rel()), Dz(), caa)
    r.run()
    assert outcome(repo).image_url == f"https://coverartarchive.org/release/{REL}/front"
    assert ("release-group", REL) not in caa.calls  # never treats a release id as a group
    rg = "44444444-4444-4444-8444-444444444444"
    caa = Caa({("release-group", rg)})
    r, repo = resolver([work], MB(Rel(release_group_mbid=rg)), Dz(), caa)
    r.run()
    assert outcome(repo).image_url == f"https://coverartarchive.org/release-group/{rg}/front"
    assert caa.calls == [("release", REL), ("release-group", rg)]
    r, repo = resolver([work], MB(None), Dz(), Caa())
    r.run()
    assert outcome(repo).status == "miss"


def test_track_falls_back_to_recording_releases_bounded_and_skips_own():
    other = "55555555-5555-4555-8555-555555555555"
    caa = Caa({("release", other)})
    mb = MB(isrcs=[], releases=[REL, other])
    r, repo = resolver([TRACK], mb, Dz(), caa)
    r.run()
    assert outcome(repo).image_url == f"https://coverartarchive.org/release/{other}/front"
    assert caa.calls == [("release", REL), ("release", other)]  # own release probed once


def test_artist_url_rel_then_wikidata_then_miss():
    wd = Wd("999")
    dz = Dz(artist=PIC)
    r, repo = resolver([ARTIST], MB(links=Links("456", "Q1")), dz, wd=wd)
    r.run()
    assert dz.calls == [("artist", "456")] and wd.calls == []
    wd, dz = Wd("999"), Dz(artist=PIC)
    r, repo = resolver([ARTIST], MB(links=Links(None, "Q1")), dz, wd=wd)
    r.run()
    assert wd.calls == ["Q1"] and dz.calls == [("artist", "999")]
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", PIC)
    dz = Dz()
    r, repo = resolver([ARTIST], MB(links=Links(None, "Q1")), dz, wd=Wd(None))
    r.run()
    assert outcome(repo).status == "miss" and dz.calls == []  # never name search / ISRC artist
    r, repo = resolver([ARTIST], MB(links=Links()), dz)
    r.run()
    assert outcome(repo).status == "miss"


def test_ttl_jitter_and_transient_backoff():
    p = ArtworkPolicy()
    lo, hi = p.ttl(True, lambda: 0.0), p.ttl(True, lambda: 0.999999)
    assert timedelta(days=24) <= lo < timedelta(days=24, hours=1)
    assert timedelta(days=35) < hi <= timedelta(days=36)
    assert p.ttl(False, lambda: 0.5) == timedelta(days=7)
    assert p.ttl(False, lambda: 0.0) < timedelta(days=6)
    assert p.backoff(1) == timedelta(seconds=60) and p.backoff(2) == timedelta(seconds=120)
    assert p.backoff(60) == timedelta(days=1)  # capped
    assert p.backoff(1, 600) == timedelta(seconds=600)
    with pytest.raises(ValueError):
        ArtworkPolicy(jitter=1)


def test_transient_never_becomes_miss_and_backs_off():
    r, repo = resolver([ALBUM], MB(error=ArtworkTransientError("x", 300)))
    repo._attempts = 2
    stats = r.run()
    work, out, delay = repo.saved[0]
    assert out.status == "transient" and out.image_url is None
    assert delay == timedelta(seconds=300) and stats["transient"] == 1 and repo.bumped == 0


def test_resolved_bumps_generation_once_and_ttl_applied():
    r, repo = resolver(
        [ALBUM, ArtworkWork("album", REL, None, 1)],
        MB(Rel()),
        caa=Caa({("release-group", MBID), ("release-group", REL)}),
    )
    stats = r.run()
    assert stats["resolved"] == 2 and repo.bumped == 1
    assert all(d == timedelta(days=30) for *_, d in repo.saved)


def test_quota_stops_run_without_saving_and_gates_next_runs():
    gate = QuotaGate()
    r, repo = resolver(
        [ALBUM, TRACK], MB(Rel("0123456789012")), Dz(error=ArtworkQuotaError("q")), quota=gate
    )
    stats = r.run()
    assert stats["quota"] == 1 and repo.saved == [] and gate.blocked(NOW)
    first = gate.blocked_until
    assert r.run()["quota"] == 1  # still gated: no work attempted
    gate.trip(NOW + timedelta(minutes=5))
    assert gate.blocked_until - (NOW + timedelta(minutes=5)) == timedelta(seconds=20)  # doubled
    gate.clear()
    assert not gate.blocked(NOW) and first is not None


def test_token_never_leaves_listenbrainz_origin(monkeypatch):
    secret = "lb-secret-token-123"
    monkeypatch.setenv("LISTENBRAINZ_TOKEN", secret)
    fake = Fake(
        {
            "musicbrainz.org/ws/2/release/": js({"barcode": "0123456789012"}),
            "api.deezer.com/album/upc": js({"cover_xl": COVER}),
            "coverartarchive.org": HttpResponse(200, {"content-type": "image/jpeg"}, b""),
            "musicbrainz.org/ws/2/artist/": js(
                {"relations": [{"url": {"resource": "https://www.wikidata.org/wiki/Q42"}}]}
            ),
            "wikidata.org": js(
                {
                    "entities": {
                        "Q42": {"claims": {"P2722": [{"mainsnak": {"datavalue": {"value": "5"}}}]}}
                    }
                }
            ),
            "api.deezer.com/artist/5": js({"picture_xl": PIC}),
            "musicbrainz.org/ws/2/recording/": js({"isrcs": ["USABC1234567"]}),
            "api.deezer.com/track": js({"album": {"cover_xl": COVER}}),
        }
    )
    cfg = MusicBrainzConfig(UA)
    repo = Repo([ALBUM, TRACK, ARTIST, ArtworkWork("track", REL, None, 1)])
    ResolveArtwork(
        repo,
        MusicBrainzArtworkLookups(cfg, fake, NoLimit()),  # type: ignore[arg-type]
        DeezerClient(UA, fake, NoLimit()),  # type: ignore[arg-type]
        CoverArtArchiveClient(UA, fake, NoLimit()),  # type: ignore[arg-type]
        WikidataClient(UA, fake, NoLimit()),  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).run()
    assert len(fake.requests) >= 8
    hosts = set()
    for req in fake.requests:
        hosts.add(req.url.split("/")[2])
        assert "listenbrainz" not in req.url
        assert secret not in req.url
        assert all(k.lower() not in {"authorization", "cookie", "x-api-key"} for k in req.headers)
        assert all(secret not in v for v in req.headers.values())
        assert req.headers["User-Agent"] == UA
    assert hosts == {"musicbrainz.org", "api.deezer.com", "coverartarchive.org", "www.wikidata.org"}


def test_job_and_cli_inert_in_fixture_mode_and_need_contact(monkeypatch):
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    job.tick()  # no DB, no network
    with pytest.raises(ValueError, match="fixture mode never calls HTTP"):
        job.run_once()
    with pytest.raises(SystemExit, match="--allow-network"):
        run(argparse.Namespace(allow_network=False, limit=20, reset_misses=False))
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.delenv("MUSIC_METADATA_USER_AGENT", raising=False)
    with pytest.raises(ValueError, match="user_agent"):
        job.run_once()
    with pytest.raises(SystemExit, match="failed safely"):
        run(argparse.Namespace(allow_network=True, limit=20, reset_misses=False))


def test_cli_registered_and_limit_validated():
    parser = argparse.ArgumentParser()
    configure(parser.add_subparsers())
    args = parser.parse_args(["resolve-artwork", "--allow-network", "--limit", "7"])
    assert args.handler is run and args.limit == 7 and args.allow_network and not args.reset_misses
    for bad in ("0", "1001"):
        with pytest.raises(SystemExit):
            parser.parse_args(["resolve-artwork", "--limit", bad])


def test_job_reduced_share_when_enrichment_busy(monkeypatch):
    calls = []
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.setattr(job, "run_once", lambda limit, **kw: calls.append(limit))
    busy = [True]
    monkeypatch.setattr(job, "_busy", lambda now: busy[0])
    monkeypatch.setattr(job, "_ticks", 0)
    for _ in range(8):
        job.tick()
    assert calls == [5, 5]  # every 4th tick, reduced batch
    busy[0] = False
    job.tick()
    assert calls[-1] == 20 and len(calls) == 3


def test_job_discovered_by_worker_registry():
    import pkgutil

    from music_dashboard.application import jobs

    assert "artwork" in {m.name for m in pkgutil.iter_modules(jobs.__path__)}
    assert callable(job.tick)


# --- name fallbacks -------------------------------------------------------------------------
from music_dashboard.domain.artwork.matching import (  # noqa: E402
    AlbumCandidate,
    ArtistCandidate,
    ArtworkNames,
    TrackCandidate,
)

OTHER = COVER.replace("b" * 32, "e" * 32)


def dz_with(searches, **kw):
    dz = Dz(**kw)
    dz.searches = searches
    return dz


def name_work(kind, names, mbid=None, key="name:x", rel=None, **kw):
    return ArtworkWork(kind, mbid, rel, 3, name_key=None if mbid else key, names=names, **kw)


def searches_of(dz):
    return [c for c in dz.calls if c[0] == "search"]


def test_exact_chains_win_and_never_search():
    names = ArtworkNames("Blue", ("Joni",))
    dz = dz_with(
        {("album", "Blue", "Joni"): [AlbumCandidate("1", "Blue", "Joni", OTHER)]}, upc=COVER
    )
    r, repo = resolver([name_work("album", names, MBID, rel=REL, album_kind="release-group")],
                       MB(Rel("0123456789012")), dz)  # fmt: skip
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", COVER)
    assert searches_of(dz) == []
    # CAA exact hit also beats name search
    dz = dz_with({("album", "Blue", "Joni"): [AlbumCandidate("1", "Blue", "Joni", OTHER)]})
    r, repo = resolver(
        [name_work("album", names, MBID)], MB(Rel()), dz, Caa({("release-group", MBID)})
    )
    r.run()
    assert outcome(repo).source == "caa" and outcome(repo).match_kind is None
    assert searches_of(dz) == []
    # exact miss -> fallback runs, with provenance
    r, repo = resolver([name_work("album", names, MBID)], MB(Rel()), dz)
    r.run()
    assert outcome(repo) == ArtworkOutcome(
        "resolved", "deezer", OTHER, None, "album_title_artist", "1"
    )


def test_name_only_entities_skip_all_exact_providers():
    mb, caa = MB(), Caa()
    dz = dz_with({("album", "Blue", "Joni"): [AlbumCandidate("4", "blue", "joni", OTHER)]})
    r, repo = resolver([name_work("album", ArtworkNames("Blue", ("Joni",)))], mb, dz, caa)
    r.run()
    assert mb.calls == [] and caa.calls == []
    assert outcome(repo).match_kind == "album_title_artist"
    assert repo.saved[0][0].ref == "name:x"


def test_album_title_only_is_last_resort_and_rejects_ambiguity_and_conflicts():
    cands = [AlbumCandidate("1", "Blue", "X", COVER), AlbumCandidate("2", "Blue", "Y", OTHER)]
    # no known artist: title-only, ambiguous -> miss
    dz = dz_with({("album", "Blue", None): cands})
    r, repo = resolver([name_work("album", ArtworkNames("Blue"))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss"
    # single candidate -> accepted as title-only
    dz = dz_with({("album", "Blue", None): cands[:1]})
    r, repo = resolver([name_work("album", ArtworkNames("Blue"))], dz=dz)
    r.run()
    assert outcome(repo).match_kind == "album_title" and outcome(repo).provider_ref == "1"
    # known artist conflicts with the only candidate -> miss; title+artist tried first
    dz = dz_with({("album", "Blue", None): cands[:1], ("album", "Blue", "Joni"): cands[:1]})
    r, repo = resolver([name_work("album", ArtworkNames("Blue", ("Joni",)))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss"
    assert [c[1:] for c in searches_of(dz)] == [("album", "Blue", "Joni"), ("album", "Blue", None)]


def test_album_search_is_bounded_and_blank_names_never_search():
    names = ArtworkNames("Blue", ("A & B", "A", "B"))
    dz = dz_with({})
    r, _ = resolver([name_work("album", names)], dz=dz)
    r.run()
    assert len(searches_of(dz)) == 3  # two artists + title-only
    for blank in (ArtworkNames(), ArtworkNames("  ", ("A",)), ArtworkNames(None, ())):
        dz = dz_with({})
        r, repo = resolver([name_work("album", blank)], dz=dz)
        r.run()
        assert searches_of(dz) == [] and outcome(repo).status == "miss"


def test_placeholder_search_results_are_misses():
    dz = dz_with({("album", "Blue", "Joni"): [AlbumCandidate("1", "Blue", "Joni", None)]})
    r, repo = resolver([name_work("album", ArtworkNames("Blue", ("Joni",)))], dz=dz)
    r.run()
    assert outcome(repo) == ArtworkOutcome("miss")
    assert repo.bumped == 0


def test_track_uses_track_search_album_cover_and_never_title_as_album():
    names = ArtworkNames("Song", ("Ann",), release="Album")
    cands = [TrackCandidate("8", "Song", "Ann", "Album", COVER)]
    dz = dz_with({("track", "Song", "Ann"): cands})
    r, repo = resolver([name_work("track", names)], dz=dz)
    r.run()
    assert outcome(repo) == ArtworkOutcome(
        "resolved", "deezer", COVER, None, "track_title_artist", "8"
    )
    # no track hit: album fallback uses the listened ALBUM title, never the track title
    dz = dz_with({("album", "Album", "Ann"): [AlbumCandidate("3", "Album", "Ann", OTHER)]})
    r, repo = resolver([name_work("track", names)], dz=dz)
    r.run()
    assert outcome(repo).image_url == OTHER
    queries = [c[1:] for c in searches_of(dz)]
    assert ("album", "Song", "Ann") not in queries and ("album", "Song", None) not in queries
    # a track titled like an existing album must not take that album's cover
    same_titled = [AlbumCandidate("3", "Song", "Ann", OTHER)]
    dz = dz_with({("album", "Song", "Ann"): same_titled, ("album", "Song", None): same_titled})
    r, repo = resolver([name_work("track", ArtworkNames("Song", ("Ann",)))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss"
    assert [c[1] for c in searches_of(dz)] == ["track"]


def test_track_without_artist_or_with_ambiguous_albums_is_a_miss():
    dz = dz_with({})
    r, repo = resolver([name_work("track", ArtworkNames("Song", (), release="Album"))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss" and searches_of(dz) == []
    ambiguous = [
        TrackCandidate("1", "Song", "Ann", "Single", COVER),
        TrackCandidate("2", "Song", "Ann", "Comp", OTHER),
    ]
    dz = dz_with({("track", "Song", "Ann"): ambiguous})
    r, repo = resolver([name_work("track", ArtworkNames("Song", ("Ann",)))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss"


def test_mbid_track_falls_back_only_after_isrc_and_caa_miss():
    names = ArtworkNames("Song", ("Ann",))
    dz = dz_with({("track", "Song", "Ann"): [TrackCandidate("8", "Song", "Ann", "A", COVER)]})
    mb = MB(isrcs=["USABC1234567"])
    r, repo = resolver([name_work("track", names, MBID)], mb, dz)
    r.run()
    assert dz.calls[0] == ("isrc", "USABC1234567") and outcome(repo).match_kind
    dz = dz_with({("track", "Song", "Ann"): [TrackCandidate("8", "Song", "Ann", "A", COVER)]},
                 isrc=OTHER)  # fmt: skip
    r, repo = resolver([name_work("track", names, MBID)], MB(isrcs=["USABC1234567"]), dz)
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", OTHER)
    assert searches_of(dz) == []


def test_artist_name_alias_and_ambiguity():
    pic = lambda i, n, u: ArtistCandidate(i, n, u)  # noqa: E731
    # name-only artist: exact name, no MusicBrainz calls
    mb = MB()
    dz = dz_with({("artist", "Muse"): [pic("1", "MUSE", COVER), pic("2", "Muse Quarterly", OTHER)]})
    r, repo = resolver([name_work("artist", ArtworkNames("Muse"))], mb, dz)
    r.run()
    assert outcome(repo).match_kind == "artist_name" and mb.calls == []
    # MBID artist: exact id chain misses -> MB name/alias search
    links = Links(name="Nat Cmiel", aliases=("Yeule",))
    dz = dz_with({("artist", "Yeule"): [pic("9", "yeule", COVER)]})
    r, repo = resolver([name_work("artist", ArtworkNames("N. C."), MBID)], MB(links=links), dz)
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", COVER, None, "artist_alias", "9")
    # ambiguous real name: alias must not rescue
    dz = dz_with({("artist", "Muse"): [pic("1", "Muse", COVER), pic("2", "Muse", OTHER)],
                  ("artist", "Yeule"): [pic("9", "Yeule", COVER)]})  # fmt: skip
    r, repo = resolver([name_work("artist", ArtworkNames("Muse", aliases=("Yeule",)))], dz=dz)
    r.run()
    assert outcome(repo).status == "miss"
    assert [c[2] for c in searches_of(dz)] == ["Muse"]


def test_exact_artist_chain_wins_over_name_search():
    dz = dz_with({("artist", "Muse"): [ArtistCandidate("1", "Muse", OTHER)]}, artist=COVER)
    r, repo = resolver([name_work("artist", ArtworkNames("Muse"), MBID)],
                       MB(links=Links(deezer_artist_id="9")), dz)  # fmt: skip
    r.run()
    assert outcome(repo) == ArtworkOutcome("resolved", "deezer", COVER)
    assert searches_of(dz) == []


def test_search_quota_stops_run_without_saving_and_transient_is_not_a_miss():
    names = ArtworkNames("Blue", ("Joni",))
    quota = QuotaGate()
    r, repo = resolver([name_work("album", names)], dz=Dz(error=ArtworkQuotaError("q", 10)),
                       quota=quota)  # fmt: skip
    assert r.run()["quota"] == 1 and repo.saved == [] and quota.blocked(NOW)
    r, repo = resolver([name_work("album", names)], dz=Dz(error=ArtworkTransientError("x")))
    assert r.run()["transient"] == 1 and outcome(repo).status == "transient"
