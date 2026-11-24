"""The single read-only artwork resolver (API role; no writes, no upstream calls).

Rankings, the public history feed and the artist endpoints all resolve cover URLs here, so the
same item shows the same cover everywhere. Keys are ranking keys: an MBID (albums: release group
or release; tracks: recording; artists: artist MBID) read from `artwork`, or a stable `name:` key
read from `artwork_name`. Artists without a photo fall back to an album cover of theirs
(`artist_cover_fallbacks`). Display only: nothing is stored and keys/labels/order never change.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from unicodedata import normalize

from sqlalchemy import Connection, text

from music_dashboard.infrastructure.artwork.store import artwork_dir, public_url
from music_dashboard.infrastructure.artwork.urls import MBID, validate_image_url
from music_dashboard.infrastructure.persistence.artist_cover_fallback import (
    artist_cover_fallbacks,
)

KINDS = ("artist", "album", "track")

_BY_MBID = """SELECT entity_mbid::text, image_url FROM artwork
 WHERE entity_type=:kind AND status='resolved' AND entity_mbid = ANY(CAST(:keys AS uuid[]))"""
_BY_NAME = """SELECT name_key, image_url FROM artwork_name
 WHERE entity_type=:kind AND status='resolved' AND name_key = ANY(CAST(:keys AS text[]))"""


def resolve_artwork(
    c: Connection, entity_type: str, keys: Iterable[str], params: dict[str, Any]
) -> dict[str, str]:
    """Map ranking key -> cover URL for the keys that have one: the local `/artwork/<key>.webp`
    copy when the worker has converted it, else the validated upstream URL.

    `params` must carry the shared `casefold` / `whitespace` fold parameters (artist fallback)."""
    if entity_type not in KINDS:
        raise ValueError(f"Unsupported artwork type: {entity_type}")
    wanted = sorted({k for k in keys if k})
    ids = [k for k in wanted if MBID.match(k)]
    names = [k for k in wanted if k.startswith("name:")]
    found: dict[str, str] = {}
    for sql, batch in ((_BY_MBID, ids), (_BY_NAME, names)):
        if batch:
            for key, url in c.execute(text(sql), {"kind": entity_type, "keys": batch}):
                valid = validate_image_url(url)
                if valid:
                    found[key] = valid
    if entity_type == "artist":
        missing = [k for k in ids + names if k not in found]
        if missing:
            found.update(artist_cover_fallbacks(c, missing, params))
    directory = artwork_dir()
    return {key: public_url(directory, url) for key, url in found.items()}


def _primary_artist_key(c: Connection, kind: str, key: str) -> str | None:
    """Primary (first credited) artist key of a ranked album/track key, None if unreliable."""
    from music_dashboard.infrastructure.persistence import artist_queries as q  # import cycle

    dimension = "tracks" if kind == "track" else "albums"
    if MBID.match(key):
        credits = q.resolve_credits(q.credit_sets(c, dimension, key))
        return credits[0]["key"] if credits else None
    try:
        pair = json.loads(key[5:])  # name:[folded artist, folded title]
    except ValueError:
        return None
    if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and pair[0]:
        return "name:" + pair[0]
    return None


def resolve_ranked_artwork(
    c: Connection,
    kind: str,
    keys: Iterable[str],
    params: dict[str, Any],
    artist_key: str | None = None,
) -> dict[str, str]:
    """Like `resolve_artwork`, but album/track rows without a cover show their primary artist's
    image (same fallback as the history feed). `artist_key` is the known artist on artist pages."""
    wanted = list(dict.fromkeys(k for k in keys if k))
    found = resolve_artwork(c, kind, wanted, params)
    if kind == "artist":
        return found
    missing = [k for k in wanted if k not in found]
    if not missing:
        return found
    primary = {k: artist_key or _primary_artist_key(c, kind, k) for k in missing}
    artists = resolve_artwork(c, "artist", {a for a in primary.values() if a}, params)
    found.update({k: artists[a] for k, a in primary.items() if a and a in artists})
    return found


@dataclass(frozen=True)
class SongKeys:
    """Ranking keys of one listen; any may be None when the listen cannot be keyed."""

    track: str | None
    album: str | None
    artist: str | None  # primary (first credited) artist


def fold(value: str | None, whitespace: str) -> str:
    """Python twin of the SQL ranking fold (NFKC, Unicode trim, full casefold)."""
    return normalize("NFKC", value or "").strip(whitespace).casefold()


def _name_key(artist: str | None, name: str | None, whitespace: str) -> str | None:
    if not fold(name, whitespace):
        return None
    return "name:" + json.dumps(
        [fold(artist, whitespace), fold(name, whitespace)], ensure_ascii=False
    )


def song_keys(
    *,
    recording_mbid: object,
    release_group_mbid: object,
    release_mbid: object,
    artist_name: str | None,
    track_name: str | None,
    release_name: str | None,
    primary_artist_key: str | None,
    whitespace: str,
) -> SongKeys:
    """Ranking keys for a listen, as the rankings build them (no canonical-album merge)."""
    album_id = release_group_mbid or release_mbid
    return SongKeys(
        track=str(recording_mbid)
        if recording_mbid
        else _name_key(artist_name, track_name, whitespace),
        album=str(album_id) if album_id else _name_key(artist_name, release_name, whitespace),
        artist=primary_artist_key,
    )


def resolve_song_artwork(
    c: Connection, songs: Sequence[SongKeys], params: dict[str, Any]
) -> list[str | None]:
    """Cover per song: its album cover, else its track cover, else its primary artist's."""
    tracks = resolve_artwork(c, "track", (s.track for s in songs if s.track), params)
    albums = resolve_artwork(c, "album", (s.album for s in songs if s.album), params)
    pending = [
        s.artist
        for s in songs
        if s.artist
        and not (s.track and s.track in tracks)
        and not (s.album and s.album in albums)
    ]
    artists = resolve_artwork(c, "artist", pending, params) if pending else {}
    return [
        (s.album and albums.get(s.album))
        or (s.track and tracks.get(s.track))
        or (s.artist and artists.get(s.artist))
        or None
        for s in songs
    ]
