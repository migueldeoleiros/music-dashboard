import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from music_dashboard.infrastructure.artwork import store

DZ = "https://cdn-images.dzcdn.net/images/cover/abc/500x500-000000-80-0-0.jpg"
CAA = "https://coverartarchive.org/release/12345678-1234-1234-1234-123456789012/front"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _jpeg(size: int = 1400) -> bytes:
    buf = io.BytesIO()
    Image.new("P", (size, size), 3).save(buf, format="PNG")  # palette image: exercises convert
    return buf.getvalue()


def test_convert_writes_all_webp_sizes_without_upscaling(tmp_path: Path) -> None:
    calls: list[str] = []

    def download(url: str) -> bytes:
        calls.append(url)
        return _jpeg(300)

    assert store.convert(tmp_path, DZ, download)
    key = store.key_for(DZ)
    for width, expected in ((1000, 300), (500, 300), (250, 250), (120, 120)):
        with Image.open(tmp_path / f"{key}-{width}.webp") as image:
            assert image.format == "WEBP" and image.size == (expected, expected)
    assert calls == [DZ.replace("500x500", "1000x1000")]
    assert not list(tmp_path.glob(".tmp-*"))
    assert store.convert(tmp_path, DZ, download) and len(calls) == 1  # already converted


def test_public_url_only_when_copy_complete(tmp_path: Path) -> None:
    assert store.public_url(None, DZ) == DZ
    assert store.public_url(tmp_path, DZ) == DZ
    (tmp_path / f"{store.key_for(DZ)}-1000.webp").write_bytes(b"x")  # partial: marker missing
    assert store.public_url(tmp_path, DZ) == DZ
    store.convert(tmp_path, DZ, lambda _u: _jpeg(200))
    assert store.public_url(tmp_path, DZ) == f"/artwork/{store.key_for(DZ)}.webp"


def test_caa_front_prefers_1200_then_original(tmp_path: Path) -> None:
    assert store.source_candidates(CAA) == [CAA + "-1200", CAA]
    seen: list[str] = []

    def download(url: str) -> bytes:
        seen.append(url)
        if url.endswith("-1200"):
            raise ValueError("http 404")
        return _jpeg(200)

    assert store.convert(tmp_path, CAA, download)
    assert seen == [CAA + "-1200", CAA]


def test_bad_image_fails_and_backs_off(tmp_path: Path) -> None:
    failed: dict[str, datetime] = {}
    attempts: list[str] = []

    def download(url: str) -> bytes:
        attempts.append(url)
        return b"not an image"

    assert store.convert_missing(tmp_path, [DZ], download, failed, NOW, 10) == 0
    assert not list(tmp_path.glob("*.webp"))
    n = len(attempts)
    assert store.convert_missing(tmp_path, [DZ], download, failed, NOW, 10) == 0
    assert len(attempts) == n  # backing off
    later = NOW + store.FAIL_BACKOFF + timedelta(seconds=1)
    assert store.convert_missing(tmp_path, [DZ], lambda _u: _jpeg(200), failed, later, 10) == 1
    assert DZ not in failed


def test_convert_missing_respects_limit(tmp_path: Path) -> None:
    urls = [f"https://cdn-images.dzcdn.net/images/cover/{i}/500x500-0-80-0-0.jpg" for i in range(3)]
    assert store.convert_missing(tmp_path, urls, lambda _u: _jpeg(200), {}, NOW, 2) == 2


@pytest.mark.parametrize("env", ["", "  "])
def test_disabled_without_dir(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("MUSIC_ARTWORK_DIR", env)
    assert store.artwork_dir() is None
