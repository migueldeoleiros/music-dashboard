"""Artwork work items and outcomes. Keys are exact MBIDs; names are never used."""

from dataclasses import dataclass
from typing import Literal

ArtworkEntityType = Literal["album", "track", "artist"]
ArtworkSource = Literal["deezer", "caa", "none"]
ArtworkStatus = Literal["resolved", "miss", "transient"]


@dataclass(frozen=True)
class ArtworkWork:
    entity_type: ArtworkEntityType
    mbid: str  # release-group (album), recording (track) or artist MBID
    # Most-listened release for the album's release group / the track's recording, if known.
    representative_release_mbid: str | None
    listens: int
    # "release-group" (normal) or "release": the album ranking key is coalesce(release group,
    # release), so albums without a release group are keyed (and stored) by release MBID.
    album_kind: Literal["release-group", "release"] = "release-group"
    # Exact CAA image ListenBrainz already resolved: (caa_release_mbid, caa_id), most frequent
    # among the entity's active raw listens. Never a name match.
    caa_hint: tuple[str, str] | None = None


@dataclass(frozen=True)
class ArtworkOutcome:
    status: ArtworkStatus
    source: ArtworkSource = "none"
    image_url: str | None = None
    retry_after: float | None = None  # transient only: server-suggested minimum delay (seconds)
