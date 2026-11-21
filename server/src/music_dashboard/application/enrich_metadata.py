"""Durable entity enrichment; cached success survives interruption before resolution writes."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID

from music_dashboard.application.ports import MusicBrainzClientPort
from music_dashboard.domain.metadata.entities import (
    CachedLookup,
    EnrichmentWork,
    EntityType,
    MetadataSource,
)
from music_dashboard.domain.metadata.errors import MetadataPermanentError, TransientMetadataError
from music_dashboard.domain.metadata.policies import GenreVote, valid_country
from music_dashboard.domain.metadata.repositories import EnrichmentRepository


class RecordingMetadataPort(Protocol):
    def recording_metadata(self, mbids: list[str]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class EnrichmentPolicy:
    empty_ttl: timedelta = timedelta(days=7)
    positive_ttl: timedelta = timedelta(days=30)
    retry_base_seconds: float = 30
    retry_max_seconds: float = 86400

    def __post_init__(self) -> None:
        if (
            min(
                self.empty_ttl.total_seconds(),
                self.positive_ttl.total_seconds(),
                self.retry_base_seconds,
                self.retry_max_seconds,
            )
            <= 0
        ):
            raise ValueError("Enrichment TTLs and retry delays must be positive")


def recognized_votes(payload: dict[str, Any], *, musicbrainz: bool = False) -> list[GenreVote]:
    """MB genre lists are controlled; LB requires explicit genre_mbid, never tag IDs."""
    rows = payload.get("genres", [])
    if not isinstance(rows, list):
        return []
    votes: dict[str, GenreVote] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        mbid = row.get("mbid", row.get("id")) if musicbrainz else row.get("genre_mbid")
        try:
            key = str(UUID(str(mbid)))
        except (ValueError, TypeError, AttributeError):
            continue
        count = row.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool):
            count = 0
        previous = votes.get(key)
        votes[key] = GenreVote(
            key, str(row.get("name") or key), count + (previous.count if previous else 0)
        )
    return list(votes.values())


class EnrichMetadata:
    def __init__(
        self,
        repository: EnrichmentRepository,
        listenbrainz: RecordingMetadataPort,
        musicbrainz: MusicBrainzClientPort,
        policy: EnrichmentPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repository = repository
        self.listenbrainz = listenbrainz
        self.musicbrainz = musicbrainz
        self.policy = policy or EnrichmentPolicy()
        self.clock = clock
        self._source_deadlines: dict[MetadataSource, datetime | None] = {}
        self._deferred_recordings: dict[str, CachedLookup] = {}

    def _remember_cooldown(self, source: MetadataSource, deadline: datetime) -> None:
        old = self._source_deadlines.get(source)
        self._source_deadlines[source] = max(old, deadline) if old else deadline

    def _store(
        self,
        work: EnrichmentWork,
        source: MetadataSource,
        payload: dict[str, Any],
    ) -> CachedLookup:
        now = self.clock()
        payload = dict(payload)
        rate_delay = payload.pop("_retry_after", None)
        if isinstance(rate_delay, (int, float)) and rate_delay > 0:
            deadline = now + timedelta(seconds=rate_delay)
            payload["_source_retry_at"] = deadline.isoformat()
            self._remember_cooldown(source, deadline)
        positive = bool(recognized_votes(payload, musicbrainz=source == "musicbrainz"))
        if work.entity_type == "artist":
            positive = positive or valid_country(payload.get("country")) is not None
        ttl = self.policy.positive_ttl if positive else self.policy.empty_ttl
        result = CachedLookup(
            work.entity_type,
            work.mbid,
            source,
            "positive" if positive else "empty",
            payload,
            now,
            now + ttl,
        )
        self.repository.save_lookup(result)
        return result

    def _failure(
        self,
        work: EnrichmentWork,
        source: MetadataSource,
        error: TransientMetadataError,
    ) -> CachedLookup:
        old = self.repository.cached(work.entity_type, work.mbid, source)
        attempts = (old.attempts if old else 0) + 1
        seconds = min(
            self.policy.retry_max_seconds,
            self.policy.retry_base_seconds * 2 ** min(attempts - 1, 20),
        )
        seconds = max(seconds, error.retry_after or 0)
        now = self.clock()
        lookup = CachedLookup(
            work.entity_type,
            work.mbid,
            source,
            "transient_error",
            old.payload if old else {},
            now,
            now + timedelta(seconds=seconds),
            attempts,
        )
        self.repository.save_lookup(lookup)
        self._remember_cooldown(source, lookup.next_retry_at)
        return lookup

    def _cooldown(self, work: EnrichmentWork, source: MetadataSource) -> CachedLookup | None:
        if source not in self._source_deadlines:
            self._source_deadlines[source] = self.repository.source_retry_at(source)
        deadline = self._source_deadlines[source]
        if deadline is None or deadline <= self.clock():
            return None
        return CachedLookup(
            work.entity_type, work.mbid, source, "transient_error", {}, self.clock(), deadline
        )

    def _lookup(self, work: EnrichmentWork, source: MetadataSource) -> CachedLookup:
        if source == "listenbrainz" and work.mbid in self._deferred_recordings:
            return self._deferred_recordings[work.mbid]
        cached = self.repository.cached(work.entity_type, work.mbid, source)
        if cached and cached.next_retry_at > self.clock():
            return cached
        cooldown = self._cooldown(work, source)
        if cooldown:
            return cooldown
        if source != "musicbrainz":
            raise RuntimeError("LB recording batch must be prefetched")
        try:
            payload = self.musicbrainz.genres(work.entity_type, work.mbid)
        except TransientMetadataError as error:
            return self._failure(work, source, error)
        except MetadataPermanentError as error:
            if not error.source_wide:
                raise
            return self._failure(work, source, TransientMetadataError("source unavailable"))
        return self._store(work, source, payload)

    def _dependencies(self, work: EnrichmentWork, lookup: CachedLookup) -> None:
        # Replay from the recording payload after interruption between cache commits.
        if lookup.status != "positive" and lookup.status != "empty":
            return
        payload: dict[str, Any] = lookup.payload
        dependencies = []
        rg = payload.get("release_group_mbid")
        if rg and payload.get("release_group_genres"):
            dependencies.append(("release_group", str(rg), payload["release_group_genres"]))
        for artist in payload.get("artist_genres", []):
            if artist.get("artist_mbid") and artist.get("genres"):
                dependencies.append(("artist", artist["artist_mbid"], artist["genres"]))
        for kind, mbid, genres in dependencies:
            # Canonicalize upstream IDs before writing UUID columns.
            try:
                mbid = str(UUID(mbid))
            except (ValueError, TypeError, AttributeError):
                continue
            votes = recognized_votes({"genres": genres})
            if not votes:
                continue
            entry = CachedLookup(
                cast(EntityType, kind),
                mbid,
                "listenbrainz",
                "positive",
                {
                    "genres": [
                        {"genre_mbid": vote.mbid, "name": vote.name, "count": vote.count}
                        for vote in sorted(votes, key=lambda row: row.mbid)
                    ]
                },
                lookup.fetched_at,
                lookup.fetched_at + self.policy.positive_ttl,
            )
            old = self.repository.cached(entry.entity_type, mbid, "listenbrainz")
            if old and (
                old.fetched_at > entry.fetched_at
                or (
                    old.fetched_at == entry.fetched_at
                    and json.dumps(old.payload, sort_keys=True)
                    >= json.dumps(entry.payload, sort_keys=True)
                )
            ):
                continue
            self.repository.save_lookup(entry)

    def _batch(self, items: list[EnrichmentWork]) -> None:
        pending = []
        for work in items:
            cached = self.repository.cached("recording", work.mbid, "listenbrainz")
            if cached is None or cached.next_retry_at <= self.clock():
                pending.append(work)
            else:
                self._dependencies(work, cached)
        for offset in range(0, len(pending), 1000):
            chunk = pending[offset : offset + 1000]
            cooldown = self._cooldown(chunk[0], "listenbrainz")
            if cooldown:
                for work in chunk:
                    self._deferred_recordings[work.mbid] = CachedLookup(
                        "recording",
                        work.mbid,
                        "listenbrainz",
                        "transient_error",
                        {},
                        cooldown.fetched_at,
                        cooldown.next_retry_at,
                    )
                continue
            try:
                rows = self.listenbrainz.recording_metadata([work.mbid for work in chunk])
            except (TransientMetadataError, MetadataPermanentError) as error:
                failure = (
                    error
                    if isinstance(error, TransientMetadataError)
                    else TransientMetadataError("LB source unavailable")
                )
                for work in chunk:
                    self._failure(work, "listenbrainz", failure)
                continue
            indexed = {str(row.get("recording_mbid") or row.get("id")): row for row in rows}
            for work in chunk:
                payload = indexed.get(work.mbid, {})
                lookup = self._store(work, "listenbrainz", payload)
                self._dependencies(work, lookup)

    def run(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("Enrichment batch limit must be 1..1000")
        self._source_deadlines.clear()
        self._deferred_recordings.clear()
        self.repository.refresh_queue(self.clock())
        items = self.repository.ready(self.clock(), limit)
        self._batch([work for work in items if work.entity_type == "recording"])
        completed = 0
        for work in items:
            lookups: list[CachedLookup] = []
            lb = self.repository.cached(work.entity_type, work.mbid, "listenbrainz")
            if work.entity_type == "recording" or (lb and lb.next_retry_at > self.clock()):
                lookups.append(self._lookup(work, "listenbrainz"))
            # Artist country is independently resolved from MB's explicit country field.
            # Do not turn a failed higher-priority source into a successful negative result.
            if (
                work.entity_type == "artist"
                or not lookups
                or (
                    lookups[-1].status != "transient_error"
                    and not recognized_votes(lookups[-1].payload)
                )
            ):
                try:
                    lookups.append(self._lookup(work, "musicbrainz"))
                except MetadataPermanentError:
                    self.repository.reject(work, self.clock())
                    continue
            failed = [lookup for lookup in lookups if lookup.status == "transient_error"]
            if failed:
                self.repository.retry(work, max(row.next_retry_at for row in failed), self.clock())
                continue
            latest = lookups[-1]
            winning = next(
                (
                    row
                    for row in lookups
                    if recognized_votes(row.payload, musicbrainz=row.source == "musicbrainz")
                ),
                latest,
            )
            votes = recognized_votes(winning.payload, musicbrainz=winning.source == "musicbrainz")
            country = (
                valid_country(latest.payload.get("country"))
                if work.entity_type == "artist"
                else None
            )
            self.repository.complete(
                work, votes, country, min(row.next_retry_at for row in lookups), self.clock()
            )
            completed += 1
        return completed
