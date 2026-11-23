"""Local WebP copies of resolved artwork (worker writes, API reads, nginx serves).

Files are content-addressed by the UPSTREAM image URL (`artwork.image_url` is unchanged):
`<dir>/<key>-<w>.webp` for w in WIDTHS, key = sha256(url)[:32]. The API swaps an upstream URL for
`/artwork/<key>.webp` only when the copy is complete; the client appends `-<w>` for its size.
Enabled only when MUSIC_ARTWORK_DIR is set; otherwise every URL stays an upstream hotlink.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from music_dashboard.infrastructure.artwork.transport import download_image

WIDTHS = (1000, 500, 250, 120)  # written largest first; the last one is the completeness marker
PUBLIC_PREFIX = "/artwork/"
MAX_DOWNLOAD_BYTES = 15_000_000
WEBP_QUALITY = 80
_DEEZER_HOSTS = frozenset({"cdn-images.dzcdn.net", "e-cdns-images.dzcdn.net"})
_DEEZER_SIZE = re.compile(r"/\d+x\d+-")
_CAA_FRONT = re.compile(r"^(/(?:release|release-group)/[0-9a-f-]{36}/front)$")

Downloader = Callable[[str], bytes]


def artwork_dir() -> Path | None:
    value = os.environ.get("MUSIC_ARTWORK_DIR", "").strip()
    return Path(value) if value else None


def key_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def _marker(directory: Path, url: str) -> Path:
    return directory / f"{key_for(url)}-{WIDTHS[-1]}.webp"


def public_url(directory: Path | None, url: str) -> str:
    """`/artwork/<key>.webp` when a complete local copy exists, else the upstream URL."""
    if directory is not None and _marker(directory, url).exists():
        return f"{PUBLIC_PREFIX}{key_for(url)}.webp"
    return url


def source_candidates(url: str) -> list[str]:
    """Largest practical upstream renditions first, then the stored URL itself."""
    parts = urlsplit(url)
    if parts.hostname in _DEEZER_HOSTS and _DEEZER_SIZE.search(parts.path):
        big = parts._replace(path=_DEEZER_SIZE.sub("/1000x1000-", parts.path)).geturl()
        return [big, url] if big != url else [url]
    if parts.hostname == "coverartarchive.org" and _CAA_FRONT.match(parts.path):
        return [parts._replace(path=parts.path + "-1200").geturl(), url]
    return [url]


def _encode(source: bytes, directory: Path, key: str) -> None:
    with Image.open(io.BytesIO(source)) as image:
        image.load()
        base = image if image.mode in ("RGB", "RGBA") else image.convert("RGB")
        for width in WIDTHS:
            variant = base.copy()
            variant.thumbnail((width, width), Image.Resampling.LANCZOS)  # never upscales
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".webp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    variant.save(handle, format="WEBP", quality=WEBP_QUALITY, method=6)
                os.chmod(tmp, 0o644)
                os.replace(tmp, directory / f"{key}-{width}.webp")
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise


def convert(directory: Path, url: str, download: Downloader) -> bool:
    """Download + write all WebP sizes for `url`. False when no candidate yielded an image."""
    if _marker(directory, url).exists():
        return True
    directory.mkdir(parents=True, exist_ok=True)
    for candidate in source_candidates(url):
        try:
            source = download(candidate)
            _encode(source, directory, key_for(url))
            return True
        except (ValueError, OSError, Image.DecompressionBombError):
            continue  # unreadable/oversized/missing rendition: try the next one
    return False


FAIL_BACKOFF = timedelta(hours=6)


def convert_missing(
    directory: Path,
    urls: Iterable[str],
    download: Downloader,
    failed: dict[str, datetime],
    now: datetime,
    limit: int,
) -> int:
    """Convert up to `limit` URLs lacking a local copy; failures back off in `failed`."""
    done = 0
    attempted = 0
    for url in urls:
        if attempted >= limit:
            break
        if _marker(directory, url).exists() or failed.get(url, now) > now:
            continue
        attempted += 1
        if convert(directory, url, download):
            failed.pop(url, None)
            done += 1
        else:
            failed[url] = now + FAIL_BACKOFF
    return done


def user_agent_downloader(user_agent: str) -> Downloader:
    return lambda url: download_image(url, user_agent, max_bytes=MAX_DOWNLOAD_BYTES)
