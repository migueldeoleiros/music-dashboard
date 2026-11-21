import json
import urllib.request

import pytest

from music_dashboard.domain.artwork.errors import ArtworkQuotaError, ArtworkTransientError
from music_dashboard.infrastructure.artwork import transport as tr
from music_dashboard.infrastructure.artwork.caa import CoverArtArchiveClient
from music_dashboard.infrastructure.artwork.deezer import EMPTY_HASH, DeezerClient, TokenBucket
from music_dashboard.infrastructure.artwork.wikidata import WikidataClient
from music_dashboard.infrastructure.musicbrainz.artwork_lookup import MusicBrainzArtworkLookups
from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    HttpResponse,
    RateLimiter,
    shared_musicbrainz_limiter,
)

UA = "music-dashboard/0.1 (mailto:test@example.invalid)"
MBID = "11111111-1111-4111-8111-111111111111"
R2 = "66666666-6666-4666-8666-666666666666"
RG = "77777777-7777-4777-8777-777777777777"
H = "b" * 32
COVER = f"https://cdn-images.dzcdn.net/images/cover/{H}/1000x1000-000000-80-0-0.jpg"
PIC = f"https://cdn-images.dzcdn.net/images/artist/{H}/1000x1000-000000-80-0-0.jpg"


class Fake:
    """Scripted transport: url-substring -> response (or exception)."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        for needle, resp in self.routes.items():
            if needle in request.url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return HttpResponse(404, {}, b"")


def js(data, status=200, headers=None):
    return HttpResponse(status, headers or {}, json.dumps(data).encode())


class NoLimit:
    def __init__(self):
        self.acquired = 0
        self.deferred = []

    def acquire(self):
        self.acquired += 1

    def defer(self, seconds):
        self.deferred.append(seconds)


def deezer(routes):
    fake = Fake(routes)
    return DeezerClient(UA, fake, NoLimit()), fake  # type: ignore[arg-type]


def test_deezer_album_track_artist_hits():
    client, fake = deezer(
        {
            "album/upc:0123456789012": js({"cover_xl": COVER}),
            "album/77": js({"cover_big": COVER}),
            "track/isrc:USABC1234567": js({"album": {"cover_xl": COVER}}),
            "artist/9": js({"picture_xl": PIC}),
        }
    )
    assert client.album_by_upc("0123456789012") == COVER
    assert client.album("77") == COVER
    assert client.track_album_cover("USABC1234567") == COVER
    assert client.artist_picture("9") == PIC
    assert all(r.url.startswith("https://api.deezer.com/") for r in fake.requests)


def test_deezer_miss_cases_and_exact_lookups_only():
    empty_artist = f"https://cdn-images.dzcdn.net/images/artist/{EMPTY_HASH}/1000x1000.jpg"
    client, fake = deezer(
        {
            "album/upc:0123456789012": js({"error": {"type": "DataException", "code": 800}}),
            "artist/9": js({"picture_xl": empty_artist, "picture_big": empty_artist}),
            "artist/10": js({"picture_xl": "https://cdn-images.dzcdn.net/images/artist//x.jpg"}),
            "track/isrc:USABC1234567": js({"error": {"code": 800}}),
        }
    )
    assert client.album_by_upc("0123456789012") is None
    assert client.artist_picture("9") is None
    assert client.artist_picture("10") is None
    assert client.track_album_cover("USABC1234567") is None
    # malformed identifiers never reach the network (no URL built from arbitrary text)
    before = len(fake.requests)
    assert client.album_by_upc("../search?q=x") is None
    assert client.album("12/../x") is None
    assert client.track_album_cover("not an isrc") is None
    assert client.artist_picture("abc") is None
    assert len(fake.requests) == before
    assert not any("search" in r.url for r in fake.requests)


def test_deezer_quota_busy_and_http_errors():
    client, _ = deezer(
        {
            "album/upc:0123456789012": js({"error": {"type": "Exception", "code": 4}}),
            "album/1": js({"error": {"code": 700}}),
            "album/2": HttpResponse(503, {"retry-after": "7"}, b""),
            "album/3": HttpResponse(200, {}, b"<html>"),
        }
    )
    with pytest.raises(ArtworkQuotaError):
        client.album_by_upc("0123456789012")
    with pytest.raises(ArtworkTransientError) as busy:
        client.album("1")
    assert not isinstance(busy.value, ArtworkQuotaError)
    with pytest.raises(ArtworkTransientError) as err:
        client.album("2")
    assert err.value.retry_after == 7
    with pytest.raises(ArtworkTransientError):
        client.album("3")


def test_token_bucket_is_well_under_deezer_quota():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    bucket = TokenBucket(5.0, 5.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):
        bucket.acquire()
    assert slept == []  # burst
    for _ in range(50):
        bucket.acquire()
    # 50 further calls need ~10 s: never more than 5/s sustained (Deezer allows 50 / 5 s)
    assert now[0] == pytest.approx(10.0, abs=0.01)
    bucket.defer(3)
    before = now[0]
    bucket.acquire()
    assert now[0] - before >= 3


def test_caa_head_returns_original_url_and_handles_statuses():
    fake = Fake(
        {f"release-group/{MBID}/front": HttpResponse(200, {"content-type": "image/jpeg"}, b"")}
    )
    caa = CoverArtArchiveClient(UA, fake, NoLimit())  # type: ignore[arg-type]
    assert (
        caa.front_url("release-group", MBID)
        == f"https://coverartarchive.org/release-group/{MBID}/front"
    )
    assert fake.requests[0].method == "HEAD"
    assert caa.front_url("release", MBID) is None  # 404
    assert caa.front_url("release", "not-a-uuid") is None
    assert caa.front_url("artist", MBID) is None
    html = Fake({"front": HttpResponse(200, {"content-type": "text/html"}, b"")})
    assert CoverArtArchiveClient(UA, html, NoLimit()).front_url("release", MBID) is None  # type: ignore[arg-type]
    down = Fake({"front": HttpResponse(503, {}, b"")})
    with pytest.raises(ArtworkTransientError):
        CoverArtArchiveClient(UA, down, NoLimit()).front_url("release", MBID)  # type: ignore[arg-type]
    net = Fake({"front": ArtworkTransientError("network error")})
    with pytest.raises(ArtworkTransientError):
        CoverArtArchiveClient(UA, net, NoLimit()).front_url("release", MBID)  # type: ignore[arg-type]


def test_caa_hint_url_is_exact_verified_and_validated():
    url = f"https://coverartarchive.org/release/{MBID}/987-500.jpg"
    fake = Fake(
        {f"release/{MBID}/987-500.jpg": HttpResponse(200, {"content-type": "image/jpeg"}, b"")}
    )
    caa = CoverArtArchiveClient(UA, fake, NoLimit())  # type: ignore[arg-type]
    assert caa.hint_url(MBID, "987") == url and fake.requests[0].method == "HEAD"
    assert caa.hint_url(MBID, "988") is None  # 404: stale hint
    for bad in (("not-a-uuid", "987"), (MBID, "../x"), (MBID, "0"), (MBID, "9" * 17)):
        assert caa.hint_url(*bad) is None
    assert len(fake.requests) == 2  # invalid hints never reach the network


def test_wikidata_first_entity_and_validation():
    body = {
        "entities": {
            "Q42": {
                "claims": {
                    "P2722": [
                        {"rank": "deprecated", "mainsnak": {"datavalue": {"value": "1"}}},
                        {"rank": "normal", "mainsnak": {"datavalue": {"value": "4050205"}}},
                        {"rank": "normal", "mainsnak": {"datavalue": {"value": "999"}}},
                    ]
                }
            },
            "Q43": {"claims": {"P2722": [{"mainsnak": {"datavalue": {"value": "5"}}}]}},
        }
    }
    fake = Fake({"Special:EntityData/Q42.json": js(body), "Q7.json": js({"entities": {"Q7": {}}})})
    wd = WikidataClient(UA, fake, NoLimit())  # type: ignore[arg-type]
    assert wd.deezer_artist_id("Q42") == "4050205"
    assert wd.deezer_artist_id("Q7") is None
    assert wd.deezer_artist_id("Q404") is None
    n = len(fake.requests)
    assert wd.deezer_artist_id("Q1/../x") is None and wd.deezer_artist_id("42") is None
    assert len(fake.requests) == n
    assert fake.requests[0].url == "https://www.wikidata.org/wiki/Special:EntityData/Q42.json"


def mb(routes, limiter=None):
    fake = Fake(routes)
    return MusicBrainzArtworkLookups(MusicBrainzConfig(UA), fake, limiter or NoLimit()), fake  # type: ignore[arg-type]


def test_musicbrainz_lookups_parse_exact_fields():
    rel = lambda url: {"url": {"resource": url}}  # noqa: E731
    lookups, fake = mb(
        {
            f"release/{MBID}": js(
                {
                    "barcode": "0123456789012",
                    "release-group": {"id": RG.upper()},
                    "relations": [rel("https://www.deezer.com/en/album/123")],
                }
            ),
            f"recording/{MBID}": js(
                {
                    "isrcs": [
                        "USABC1234567",
                        "bad",
                        "GBXYZ7654321",
                        "FRAAA0000001",
                        "ITBBB1111111",
                    ],
                    "releases": [{"id": R2.upper()}, {"id": "bad"}, {"id": R2}, {"id": MBID}]
                    + [{"id": f"00000000-0000-4000-8000-00000000000{i}"} for i in range(6)],
                }
            ),
            f"artist/{MBID}": js(
                {
                    "relations": [
                        rel("https://www.wikidata.org/wiki/Q42"),
                        rel("https://www.deezer.com/artist/456"),
                        rel("https://evil.example/deezer.com/artist/9"),
                    ]
                }
            ),
        }
    )
    info = lookups.release(MBID)
    assert info and info.barcode == "0123456789012" and info.deezer_album_id == "123"
    assert info.release_group_mbid == RG
    rec = lookups.recording(MBID)
    assert rec.isrcs == ["USABC1234567", "GBXYZ7654321", "FRAAA0000001"]
    assert rec.release_mbids[:2] == [R2, MBID] and len(rec.release_mbids) == 5  # dedup + bounded
    links = lookups.artist_links(MBID)
    assert (links.deezer_artist_id, links.wikidata_qid) == ("456", "Q42")
    assert [("inc=url-rels" in r.url, "inc=isrcs+releases" in r.url) for r in fake.requests] == [
        (True, False),
        (False, True),
        (True, False),
    ]
    assert "inc=url-rels+release-groups" in fake.requests[0].url
    assert lookups.release("22222222-2222-4222-8222-222222222222") is None  # 404
    assert lookups.release("../../x") is None and len(fake.requests) == 4


def test_musicbrainz_uses_shared_limiter_and_honors_503():
    lookups = MusicBrainzArtworkLookups(MusicBrainzConfig(UA), Fake({}))  # type: ignore[arg-type]
    assert lookups._limiter is shared_musicbrainz_limiter()
    limiter = NoLimit()
    lookups, _ = mb({f"release/{MBID}": HttpResponse(503, {"retry-after": "30"}, b"")}, limiter)
    with pytest.raises(ArtworkTransientError) as err:
        lookups.release(MBID)
    assert err.value.retry_after == 30 and 30 in limiter.deferred and limiter.acquired == 1
    real = RateLimiter(1.0, clock=lambda: 0.0, sleep=lambda s: None)
    lookups, _ = mb(
        {
            f"release/{MBID}": HttpResponse(
                200, {"x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "5"}, b"{}"
            )
        },
        real,
    )
    lookups.release(MBID)
    assert real._next_allowed >= 5


def test_transport_refuses_credentials_and_foreign_hosts():
    ok = HttpRequest("GET", "https://api.deezer.com/album/1", {"User-Agent": UA})
    tr.check_request(ok)
    for headers in ({"Authorization": "Token secret"}, {"cookie": "a=b"}, {"X-Api-Key": "k"}):
        with pytest.raises(ValueError):
            tr.check_request(HttpRequest("GET", ok.url, headers))
    for url in (
        "http://api.deezer.com/album/1",
        "https://api.listenbrainz.org/1/user/x/listens",
        "https://evil-archive.org/x",
        "https://archive.org.evil.com/x",
        "https://user@api.deezer.com/x",
        "https://api.deezer.com:8443/x",
    ):
        with pytest.raises(ValueError):
            tr.check_request(HttpRequest("GET", url, {}))
    for url in (
        "https://musicbrainz.org/ws/2/release/x",
        "https://www.wikidata.org/wiki/Special:EntityData/Q1.json",
        "https://coverartarchive.org/release/x/front",
        "https://ia800.us.archive.org/x.jpg",
    ):
        tr.check_request(HttpRequest("HEAD", url, {"User-Agent": UA}))


def test_redirects_only_to_allowlisted_https_hosts():
    handler = tr._AllowlistedRedirects()
    req = urllib.request.Request("https://coverartarchive.org/release/x/front", method="HEAD")
    follow = handler.redirect_request(
        req, None, 307, "x", {}, "https://archive.org/download/x/y.jpg"
    )
    assert follow is not None and follow.get_method() == "HEAD"
    for target in (
        "http://archive.org/x",
        "https://evil.example/x",
        "https://archive.org.evil.com/x",
        "https://evil-archive.org/x",
    ):
        assert handler.redirect_request(req, None, 307, "x", {}, target) is None
