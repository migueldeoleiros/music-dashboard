import pytest

from music_dashboard.infrastructure.artwork.deezer import EMPTY_HASH, usable_image
from music_dashboard.infrastructure.artwork.urls import validate_image_url

H = "a" * 32


@pytest.mark.parametrize(
    "url",
    [
        f"https://cdn-images.dzcdn.net/images/cover/{H}/1000x1000-000000-80-0-0.jpg",
        f"https://e-cdns-images.dzcdn.net/images/cover/{H}/250x250-000000-80-0-0.jpg",
        "https://coverartarchive.org/release-group/abc/front",
        "https://archive.org/download/x/y.jpg",
        "https://ia800000.us.archive.org/1/items/x/y.jpg",
    ],
)
def test_allowed(url):
    assert validate_image_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        42,
        "http://coverartarchive.org/x",  # not https
        "//coverartarchive.org/x",
        "https://evil-archive.org/x",  # lookalike
        "https://archive.org.evil.com/x",
        "https://coverartarchive.org.evil.com/x",
        "https://notcoverartarchive.org/x",
        "https://.archive.org/x",
        "https://archive.org./x",
        "https://user@archive.org/x",  # userinfo
        "https://user:pw@archive.org/x",
        "https://archive.org:8443/x",  # port
        "https://archive.org:443/x",
        "https://archive.org@evil.com/x",
        "https://evil.com\\@archive.org/x",
        "https://evil.com#@archive.org/x",
        "https://archive.org/x y",
        "https://archive.org/x\n",
        "https://archive.org/x#frag",
        "https://ARCHIVE.org/x",
        "https://cdn-images.dzcdn.net.evil.com/x",
        "https://xn--archive-9wa.org/x",
        "https://archive.org",  # no path
        "https://archive.org/" + "a" * 2100,
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
    ],
)
def test_rejected(url):
    assert validate_image_url(url) is None


def test_deezer_empty_hash_and_shape():
    ok = f"https://cdn-images.dzcdn.net/images/artist/{H}/1000x1000-000000-80-0-0.jpg"
    assert usable_image(ok) == ok
    for bad in (
        f"https://cdn-images.dzcdn.net/images/artist/{EMPTY_HASH}/1000x1000-000000-80-0-0.jpg",
        "https://e-cdns-images.dzcdn.net/images/artist//1000x1000-000000-80-0-0.jpg",
        "https://cdn-images.dzcdn.net/images/other/x/y.jpg",
        f"https://evil.example/images/cover/{H}/x.jpg",
        None,
    ):
        assert usable_image(bad) is None
