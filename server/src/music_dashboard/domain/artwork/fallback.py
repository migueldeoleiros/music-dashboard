"""Pure policy: reuse an existing album cover as an artist's display image. No I/O.

Read-time only. The cover is never stored as the artist's photo; ranking keys, labels, totals and
ordering never depend on it. An album is eligible for an artist only when the artist is clearly
its main credit: credited on MORE THAN half of all known listens of that album, and the album is
not a various-artists compilation. Identity stays exact: MBID credits match by MBID only, name-only
credits match by folded name only (an MBID artist never borrows a same-named unmapped artist's
albums and vice versa). Most-played eligible album wins; ties break by album key ascending.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

VARIOUS_ARTISTS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"
VARIOUS_ARTISTS_NAMES = frozenset({"various artists"})


@dataclass(frozen=True)
class AlbumRef:
    """Canonical album key: an MBID (release group, else release) or a folded name pair."""

    kid: str | None = None
    artist: str | None = None
    album: str | None = None

    @property
    def sort_key(self) -> str:
        return self.kid or f"name:{self.artist}\x00{self.album}"


@dataclass(frozen=True)
class AlbumStats:
    ref: AlbumRef
    total: int  # all known listens of the album
    credited: int  # listens credited to the artist being resolved
    various: bool = False  # any listen credits Various Artists

    @property
    def eligible(self) -> bool:
        return not self.various and self.credited > 0 and self.credited * 2 > self.total


def order_eligible(stats: Iterable[AlbumStats]) -> list[AlbumStats]:
    """Eligible albums, most credited plays first, stable tie-break by album key."""
    return sorted((s for s in stats if s.eligible), key=lambda s: (-s.credited, s.ref.sort_key))


def pick_cover(
    stats: Iterable[AlbumStats],
    covers: Mapping[AlbumRef, str],
    validate: Callable[[object], str | None],
) -> str | None:
    """First eligible album (by play count) that has a cover passing URL validation."""
    for s in order_eligible(stats):
        url = validate(covers.get(s.ref))
        if url:
            return url
    return None


def resolve_image(
    photo: str | None,
    stats: Iterable[AlbumStats],
    covers: Mapping[AlbumRef, str],
    validate: Callable[[object], str | None],
) -> str | None:
    """Precedence: resolved artist photo, then album cover, else None (client shows initials)."""
    return validate(photo) or pick_cover(stats, covers, validate)
