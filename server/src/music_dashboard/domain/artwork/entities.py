"""Artwork work items and outcomes.

Every work is keyed by an exact MBID, or, for ranking entities that have no MBID at all, by the
stable ranking key `name:...` (`name_key`). Names (`names`) are only ever used AFTER every exact
chain misses, and only to look up an image URL: they never create, merge or rename an identity.
"""

from dataclasses import dataclass
from typing import Literal

from music_dashboard.domain.artwork.matching import ArtworkNames

ArtworkEntityType = Literal["album", "track", "artist"]
ArtworkSource = Literal["deezer", "caa", "none"]
ArtworkStatus = Literal["resolved", "miss", "transient"]


@dataclass(frozen=True)
class ArtworkWork:
    entity_type: ArtworkEntityType
    # release-group (album), recording (track) or artist MBID; None for name-only entities.
    mbid: str | None
    # Most-listened release for the album's release group / the track's recording, if known.
    representative_release_mbid: str | None
    listens: int
    # "release-group" (normal) or "release": the album ranking key is coalesce(release group,
    # release), so albums without a release group are keyed (and stored) by release MBID.
    album_kind: Literal["release-group", "release"] = "release-group"
    # Exact CAA image ListenBrainz already resolved: (caa_release_mbid, caa_id), most frequent
    # among the entity's active raw listens. Never a name match.
    caa_hint: tuple[str, str] | None = None
    # Stable ranking key (`name:...`) of a name-only entity; set iff mbid is None.
    name_key: str | None = None
    # Names known from stored listens, used by the name fallback only.
    names: ArtworkNames = ArtworkNames()

    @property
    def ref(self) -> str:
        """Stable storage key: the MBID, or the `name:` ranking key."""
        value = self.mbid if self.mbid is not None else self.name_key
        if value is None:
            raise ValueError("artwork work needs an mbid or a name_key")
        return value


@dataclass(frozen=True)
class ArtworkOutcome:
    status: ArtworkStatus
    source: ArtworkSource = "none"
    image_url: str | None = None
    retry_after: float | None = None  # transient only: server-suggested minimum delay (seconds)
    # Provenance. match_kind None = the exact-ID chain; otherwise a matching.MATCH_KINDS value
    # and the provider's id of the matched entity.
    match_kind: str | None = None
    provider_ref: str | None = None
