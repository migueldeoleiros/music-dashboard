"""Pure name-matching policy for artwork fallbacks (Koito-style), no I/O.

A candidate is accepted only when its normalized name equals a known name EXACTLY (no fuzzy or
substring matching), it carries a usable image, it does not clearly conflict with known credits,
and it is unambiguous: every title/artist-equal candidate must resolve to the SAME image. Any
second distinct image (or a placeholder next to a real one) rejects the whole lookup. A match is
only ever an image URL for a ranking key: it never creates, merges or renames an identity.
"""

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Provenance values stored with a resolved name-matched image (artwork.match_kind).
ARTIST_NAME = "artist_name"
ARTIST_ALIAS = "artist_alias"
ALBUM_TITLE_ARTIST = "album_title_artist"
ALBUM_TITLE = "album_title"
TRACK_TITLE_ARTIST = "track_title_artist"
MATCH_KINDS = (ARTIST_NAME, ARTIST_ALIAS, ALBUM_TITLE_ARTIST, ALBUM_TITLE, TRACK_TITLE_ARTIST)

MAX_NAME = 200


def fold(text: str | None) -> str:
    """NFKC, casefold, collapse whitespace. Empty string means 'no usable name'."""
    if not text:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def folded_set(names: Iterable[str | None]) -> frozenset[str]:
    return frozenset(f for f in (fold(n) for n in names) if f)


@dataclass(frozen=True)
class ArtworkNames:
    """Names known for a work, taken from stored listens / MusicBrainz aliases (read-only)."""

    title: str | None = None  # album: release name; track: track name; artist: artist name
    artists: tuple[str, ...] = ()  # album/track: credited artist names (never track titles)
    aliases: tuple[str, ...] = ()  # artist: trusted MusicBrainz aliases
    release: str | None = None  # track: listened album title, only to prefer candidates


@dataclass(frozen=True)
class ArtistCandidate:
    id: str
    name: str
    image: str | None


@dataclass(frozen=True)
class AlbumCandidate:
    id: str
    title: str
    artist: str
    image: str | None


@dataclass(frozen=True)
class TrackCandidate:
    id: str
    title: str
    artist: str
    album_title: str
    image: str | None  # the track's album cover


@dataclass(frozen=True)
class Match:
    kind: str
    ref: str  # provider id of the matched entity (provenance)
    image_url: str


def _unique(kind: str, hits: list[tuple[str, str | None]]) -> Match | None:
    """hits: (provider id, usable image or None). One distinct image or reject."""
    if not hits:
        return None
    images = {image for _, image in hits}
    if len(images) != 1 or None in images:
        return None  # ambiguous, or only placeholders
    ref = min(hit_id for hit_id, _ in hits)
    return Match(kind, ref, next(iter(images)))  # type: ignore[arg-type]


def match_artist(
    candidates: Iterable[ArtistCandidate], name: str | None, aliases: Iterable[str] = ()
) -> Match | None:
    """Exact (normalized) name first; trusted aliases only when the name itself finds nothing."""
    pool = list(candidates)
    primary = fold(name)
    if primary:
        hits = [(c.id, c.image) for c in pool if fold(c.name) == primary]
        if hits:
            return _unique(ARTIST_NAME, hits)  # a present-but-ambiguous name never falls to alias
    alias_set = folded_set(aliases) - {primary}
    hits = [(c.id, c.image) for c in pool if fold(c.name) in alias_set]
    return _unique(ARTIST_ALIAS, hits)


def match_album(
    candidates: Iterable[AlbumCandidate], title: str | None, artists: Iterable[str | None]
) -> Match | None:
    """Title must be equal. With known artists the candidate artist must be one of them (a
    different artist is a clear conflict). Without known artists this is the title-only last
    resort, still rejected when the title maps to more than one image."""
    wanted, known = fold(title), folded_set(artists)
    if not wanted:
        return None
    hits = [
        (c.id, c.image)
        for c in candidates
        if fold(c.title) == wanted and (not known or fold(c.artist) in known)
    ]
    return _unique(ALBUM_TITLE_ARTIST if known else ALBUM_TITLE, hits)


def match_track(
    candidates: Iterable[TrackCandidate],
    title: str | None,
    artists: Iterable[str | None],
    release: str | None = None,
) -> Match | None:
    """Track title AND a known artist are required; the result is the track's album cover.
    A listened album title only narrows candidates; it is never matched against the track
    title and an album title never matches a track."""
    wanted, known = fold(title), folded_set(artists)
    if not wanted or not known:
        return None
    pool = [c for c in candidates if fold(c.title) == wanted and fold(c.artist) in known]
    album = fold(release)
    same_album = [c for c in pool if album and fold(c.album_title) == album]
    return _unique(TRACK_TITLE_ARTIST, [(c.id, c.image) for c in (same_album or pool)])
