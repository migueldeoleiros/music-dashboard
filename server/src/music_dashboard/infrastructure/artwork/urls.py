"""Strict validation of image URLs and identifiers used to build upstream URLs.

Defence in depth: nothing is stored or returned unless it is https on an allowlisted host.
Upstream request URLs are built only from validated MBIDs / numeric ids / digits-only barcodes,
never from user text or names.
"""

import re
from urllib.parse import urlsplit

EXACT_HOSTS = frozenset(
    {"cdn-images.dzcdn.net", "e-cdns-images.dzcdn.net", "coverartarchive.org", "archive.org"}
)
SUFFIX_HOSTS = (".archive.org",)
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_MAX_URL = 2048

MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CAA_ID = re.compile(r"^[1-9][0-9]{0,15}$")
QID = re.compile(r"^Q[1-9][0-9]{0,15}$")
NUMERIC_ID = re.compile(r"^[1-9][0-9]{0,15}$")
BARCODE = re.compile(r"^[0-9]{8,14}$")
ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def host_allowed(host: str) -> bool:
    if host in EXACT_HOSTS:
        return True
    return any(host.endswith(s) and len(host) > len(s) for s in SUFFIX_HOSTS)


def validate_image_url(value: object) -> str | None:
    """Return the URL unchanged when safe, else None."""
    if not isinstance(value, str) or not value or len(value) > _MAX_URL:
        return None
    if any(ord(ch) <= 0x20 or ord(ch) >= 0x7F or ch == "\\" for ch in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or port is not None or parts.fragment:
        return None
    netloc = parts.netloc
    if "@" in netloc or ":" in netloc or parts.username or parts.password:
        return None
    host = netloc  # exact string, no userinfo/port can hide inside it
    if host != host.lower() or not _HOST.match(host) or not host_allowed(host):
        return None
    if not parts.path.startswith("/"):
        return None
    return value


def normalize_mbid(value: object) -> str | None:
    text = str(value).lower() if value is not None else ""
    return text if MBID.match(text) else None
