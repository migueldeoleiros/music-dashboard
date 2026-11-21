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


@dataclass
class Links:
    deezer_artist_id: str | None = None
    wikidata_qid: str | None = None


class MB:
    def __init__(self, release=None, isrcs=(), links=None, error=None):
        self._release, self._isrcs, self._links, self.error = release, list(isrcs), links, error
        self.calls = []

    def release(self, mbid):
        self.calls.append(("release", mbid))
        if self.error:
            raise self.error
        return self._release

    def recording_isrcs(self, mbid):
        self.calls.append(("isrcs", mbid))
        return self._isrcs

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


class Caa:
    def __init__(self, hits=()):
        self.hits, self.calls = set(hits), []

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
