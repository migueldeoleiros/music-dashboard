import json

import pytest

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
)
from music_dashboard.infrastructure.listenbrainz.metadata import (
    ListenBrainzMetadataClient,
    ListenBrainzMetadataConfig,
    recognized_genres,
)
from music_dashboard.infrastructure.musicbrainz.client import (
    MusicBrainzClient,
    MusicBrainzConfig,
)
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    HttpResponse,
    RateLimiter,
    shared_musicbrainz_limiter,
)

MBID = "11111111-2222-3333-4444-555555555555"
UA = "music-dashboard/0.1 ( me@example.org )"


class Fake:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    def __call__(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


def resp(status=200, body=None, headers=None):
    return HttpResponse(status, headers or {}, json.dumps(body or {}).encode())


class Clock:
    def __init__(self):
        self.t = 100.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def mb(transport, limiter=None):
    return MusicBrainzClient(MusicBrainzConfig(UA), transport, limiter or RateLimiter(0))


def test_limiter_spaces_calls():
    c = Clock()
    lim = RateLimiter(1.0, c.now, c.sleep)
    lim.acquire()
    lim.acquire()
    lim.acquire()
    assert c.sleeps == [1.0, 1.0]
    c.t += 5
    lim.acquire()
    assert c.sleeps == [1.0, 1.0]


def test_shared_limiter_is_global():
    assert shared_musicbrainz_limiter() is shared_musicbrainz_limiter()
    a = MusicBrainzClient(MusicBrainzConfig(UA), Fake())
    b = MusicBrainzClient(MusicBrainzConfig(UA), Fake())
    assert a._limiter is b._limiter


def test_mb_limiter_applied_and_request_shape():
    c = Clock()
    t = Fake(resp(body={"genres": []}), resp(body={"genres": []}))
    client = mb(t, RateLimiter(1.0, c.now, c.sleep))
    client.genres("release_group", MBID)
    client.genres("artist", MBID)
    assert c.sleeps == [1.0]
    assert "/release-group/" in t.requests[0].url and "inc=genres" in t.requests[0].url
    assert t.requests[0].headers["User-Agent"] == UA


def test_mb_genres_parsed_and_404_empty():
    t = Fake(resp(body={"genres": [{"id": "g1", "name": "rock", "count": 3}]}), resp(404))
    client = mb(t)
    assert client.genres("recording", MBID)["genres"] == [
        {"mbid": "g1", "name": "rock", "count": 3}
    ]
    assert client.genres("recording", MBID)["genres"] == []


def test_mb_retry_after_and_defer():
    c = Clock()
    lim = RateLimiter(1.0, c.now, c.sleep)
    t = Fake(resp(503, headers={"retry-after": "30"}))
    with pytest.raises(MetadataTransientError) as ei:
        mb(t, lim).genres("artist", MBID)
    assert ei.value.retry_after == 30.0
    lim.acquire()
    assert c.sleeps and c.sleeps[0] >= 29


def test_mb_rate_header_defers():
    c = Clock()
    lim = RateLimiter(1.0, c.now, c.sleep)
    t = Fake(resp(body={}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "7"}))
    mb(t, lim).genres("artist", MBID)
    lim.acquire()
    assert c.sleeps[-1] >= 6


def test_mb_errors_do_not_leak():
    for status, exc in ((500, MetadataTransientError), (400, MetadataPermanentError)):
        with pytest.raises(exc) as ei:
            mb(Fake(resp(status))).genres("artist", MBID)
        assert "http" in str(ei.value).lower() and MBID not in str(ei.value)
    with pytest.raises(MetadataPermanentError):
        mb(Fake()).genres("artist", "../x")
    with pytest.raises(MetadataPermanentError):
        mb(Fake()).genres("label", MBID)


def test_mb_config_requires_contact():
    with pytest.raises(ValueError):
        MusicBrainzConfig("python-urllib")
    assert UA not in repr(MusicBrainzConfig(UA))


def test_lb_batch_request_and_result():
    t = Fake(resp(body={MBID: {"artist": {"name": "A"}}}))
    c = ListenBrainzMetadataClient(ListenBrainzMetadataConfig(UA, token="sekret"), t)
    out = c.recording_metadata([MBID, "unknown"])
    assert out[0]["recording_mbid"] == MBID and out[0]["genres"] == []
    assert out[0]["metadata"] == {"artist": {"name": "A"}}
    req = t.requests[0]
    body = json.loads(req.body)
    assert req.method == "POST" and req.url.endswith("/1/metadata/recording/")
    assert body["inc"] == "artist tag release recording release_group"
    assert "sekret" not in repr(req) and "sekret" not in repr(c._config)


def test_lb_batch_limit_and_empty():
    c = ListenBrainzMetadataClient(ListenBrainzMetadataConfig(UA), Fake())
    assert c.recording_metadata([]) == []
    with pytest.raises(ValueError):
        c.recording_metadata([MBID] * 1001)


def test_lb_errors():
    cfg = ListenBrainzMetadataConfig(UA, token="sekret")
    with pytest.raises(MetadataTransientError) as ei:
        ListenBrainzMetadataClient(
            cfg, Fake(resp(429, headers={"retry-after": "12"}))
        ).recording_metadata([MBID])
    assert ei.value.retry_after == 12.0 and "sekret" not in str(ei.value)
    with pytest.raises(MetadataPermanentError):
        ListenBrainzMetadataClient(cfg, Fake(resp(401))).recording_metadata([MBID])


def test_recognized_genres_rejects_free_tags():
    tags = [
        {"tag": "rock", "count": 3, "genre_mbid": "g1"},
        {"tag": "seen-live", "count": 9},
        {"tag": "rock", "count": 2, "genre_mbid": "g1"},
    ]
    assert recognized_genres(tags) == [{"genre_mbid": "g1", "name": "rock", "count": 5}]
    assert recognized_genres(None) == []


def test_lb_normalized_shape():
    meta = {
        "artist": {"artists": [{"artist_mbid": "a1", "name": "A"}]},
        "release": {"release_group_mbid": "rg1"},
        "tag": {
            "recording": [
                {"tag": "rock", "count": 2, "genre_mbid": "g1"},
                {"tag": "x", "count": 1},
            ],
            "release_group": [{"tag": "pop", "count": 1, "genre_mbid": "g2"}],
            "artist": [
                {"tag": "jazz", "count": 4, "genre_mbid": "g3", "artist_mbid": "a1"},
                {"tag": "seen-live", "count": 4, "artist_mbid": "a1"},
            ],
        },
    }
    e = ListenBrainzMetadataClient(
        ListenBrainzMetadataConfig(UA), Fake(resp(body={MBID: meta}))
    ).recording_metadata([MBID])[0]
    assert e["genres"] == [{"genre_mbid": "g1", "name": "rock", "count": 2}]
    assert e["release_group_genres"][0]["genre_mbid"] == "g2"
    assert e["artist_genres"] == [
        {"artist_mbid": "a1", "genres": [{"genre_mbid": "g3", "name": "jazz", "count": 4}]}
    ]
    assert e["artists"] == [{"artist_mbid": "a1", "name": "A"}] and e["release_group_mbid"] == "rg1"


def test_mb_artist_country_and_invalid_json_transient():
    t = Fake(resp(body={"country": "GB", "area": {"iso-3166-1-codes": ["GB"]}, "genres": []}))
    r = mb(t).genres("artist", MBID)
    assert r["country"] == "GB" and r["area_iso_3166_1_codes"] == ["GB"]
    with pytest.raises(MetadataTransientError):
        mb(Fake(HttpResponse(200, {}, b"<html>"))).genres("artist", MBID)


def test_malformed_bodies_are_transient_not_empty():
    cfg = ListenBrainzMetadataConfig(UA)
    for meta in (
        {"tag": {"recording": [{"genre_mbid": "g", "count": "x"}]}},
        {"tag": {"recording": {"a": 1}}},
        {"tag": {"artist": "oops"}},
        {"artist": {"artists": 5}},
    ):
        with pytest.raises(MetadataTransientError):
            ListenBrainzMetadataClient(cfg, Fake(resp(body={MBID: meta}))).recording_metadata(
                [MBID]
            )
    with pytest.raises(MetadataTransientError):
        ListenBrainzMetadataClient(cfg, Fake(resp(body=[1]))).recording_metadata([MBID])
    for body in ({"genres": "x"}, {"genres": [1]}, {"genres": [{"id": "g", "count": "n"}]}):
        with pytest.raises(MetadataTransientError):
            mb(Fake(resp(body=body))).genres("artist", MBID)


def test_lb_null_tag_and_inc_space_separated():
    t = Fake(resp(body={MBID: {"tag": None, "recording": {}}}))
    e = ListenBrainzMetadataClient(ListenBrainzMetadataConfig(UA), t).recording_metadata([MBID])
    assert e[0]["genres"] == [] and e[0]["release_group_mbid"] is None
    assert json.loads(t.requests[0].body)["inc"] == "artist tag release recording release_group"
