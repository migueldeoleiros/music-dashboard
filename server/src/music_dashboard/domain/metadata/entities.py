from dataclasses import dataclass
from datetime import datetime
from typing import Literal

EntityType = Literal["recording", "release_group", "artist"]
CacheStatus = Literal["pending", "positive", "empty", "transient_error"]


@dataclass(frozen=True)
class MetadataEntry:
    entity_type: EntityType
    mbid: str
    payload: dict[str, object]
    status: CacheStatus
    fetched_at: datetime | None
    next_retry_at: datetime | None


MetadataSource = Literal["listenbrainz", "musicbrainz"]


@dataclass(frozen=True)
class CachedLookup:
    entity_type: EntityType
    mbid: str
    source: MetadataSource
    status: CacheStatus
    payload: dict[str, object]
    fetched_at: datetime
    next_retry_at: datetime
    attempts: int = 0


@dataclass(frozen=True)
class EnrichmentWork:
    entity_type: EntityType
    mbid: str
    priority: int
