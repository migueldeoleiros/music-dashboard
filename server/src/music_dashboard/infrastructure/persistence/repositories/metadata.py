"""PostgreSQL enrichment cache, frequency queue and entity resolution projections.

No per-listen weights table exists in the frozen schema. Entity votes/countries are the
resolution source; domain policies (and statistics SQL) derive per-listen fractions.
Callers hold the enrichment advisory lock across a run, not a transaction across HTTP.
"""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from music_dashboard.domain.metadata.entities import (
    CachedLookup,
    CacheStatus,
    EnrichmentWork,
    EntityType,
    MetadataSource,
)
from music_dashboard.domain.metadata.policies import GenreVote
from music_dashboard.infrastructure.persistence.models import (
    ArtistCountry,
    DataGeneration,
    EnrichmentQueueItem,
    EntityGenre,
    Genre,
    MetadataCache,
)

# Session-level lock: crash automatically releases it; no in-progress queue lease needed.
ENRICHMENT_LOCK = 730004


class SqlMetadataRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def refresh_queue(self, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO enrichment_queue (entity_type, entity_mbid, priority)
                SELECT entity_type, entity_mbid, count(DISTINCT listen_id)::integer
                FROM (
                    SELECT 'recording' AS entity_type, recording_mbid AS entity_mbid,
                           id AS listen_id FROM listens WHERE recording_mbid IS NOT NULL
                    UNION ALL
                    SELECT 'release_group', release_group_mbid, id FROM listens
                           WHERE release_group_mbid IS NOT NULL
                    UNION ALL
                    SELECT 'artist', artist_mbid, listen_id FROM listen_artists
                           WHERE artist_mbid IS NOT NULL
                ) entities
                GROUP BY entity_type, entity_mbid
                ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET priority = EXCLUDED.priority
            """)
            )
            # Recover leases left by any older host implementation.
            connection.execute(
                text("""
                UPDATE enrichment_queue SET status='pending', locked_at=NULL
                WHERE status='in_progress' AND
                    (locked_at IS NULL OR locked_at < :now - interval '1 hour')
            """),
                {"now": now},
            )

    def ready(self, now: datetime, limit: int) -> list[EnrichmentWork]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(EnrichmentQueueItem)
                .where(EnrichmentQueueItem.status != "in_progress")
                .where(
                    (EnrichmentQueueItem.status != "failed")
                    | EnrichmentQueueItem.next_retry_at.is_not(None)
                )
                .where(
                    (EnrichmentQueueItem.next_retry_at.is_(None))
                    | (EnrichmentQueueItem.next_retry_at <= now)
                )
                .order_by(EnrichmentQueueItem.priority.desc(), EnrichmentQueueItem.id)
                .limit(limit)
            )
            return [
                EnrichmentWork(
                    cast(EntityType, row.entity_type), str(row.entity_mbid), row.priority
                )
                for row in rows
            ]

    def cached(
        self,
        entity_type: EntityType,
        mbid: str,
        source: MetadataSource,
    ) -> CachedLookup | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(MetadataCache).where(
                    MetadataCache.entity_type == entity_type,
                    MetadataCache.entity_mbid == UUID(mbid),
                    MetadataCache.source == source,
                )
            )
            if row is None or row.next_retry_at is None:
                return None  # legacy/synthetic cache without expiry must be fetched
            return CachedLookup(
                entity_type,
                mbid,
                source,
                cast(CacheStatus, row.status),
                row.payload or {},
                row.fetched_at,
                row.next_retry_at,
                row.attempts,
            )

    def save_lookup(self, lookup: CachedLookup) -> None:
        values = dict(
            entity_type=lookup.entity_type,
            entity_mbid=UUID(lookup.mbid),
            source=lookup.source,
            status=lookup.status,
            payload=lookup.payload,
            fetched_at=lookup.fetched_at,
            next_retry_at=lookup.next_retry_at,
            attempts=lookup.attempts,
            last_error="upstream transient failure" if lookup.status == "transient_error" else None,
        )
        with self.engine.begin() as connection:
            previous = connection.scalar(
                select(MetadataCache.payload).where(
                    MetadataCache.entity_type == lookup.entity_type,
                    MetadataCache.entity_mbid == UUID(lookup.mbid),
                    MetadataCache.source == lookup.source,
                )
            )
            connection.execute(
                insert(MetadataCache)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["entity_type", "entity_mbid", "source"],
                    set_=values,
                )
            )
            if (
                lookup.source == "listenbrainz"
                and lookup.status == "positive"
                and (previous or {}).get("genres") != lookup.payload.get("genres")
            ):
                # Preferred source changed: project cached tags promptly, without HTTP.
                # Active source-wide cooldown is still enforced by the application.
                connection.execute(
                    update(EnrichmentQueueItem)
                    .where(
                        EnrichmentQueueItem.entity_type == lookup.entity_type,
                        EnrichmentQueueItem.entity_mbid == UUID(lookup.mbid),
                    )
                    .values(
                        status="pending",
                        next_retry_at=lookup.fetched_at,
                        updated_at=lookup.fetched_at,
                    )
                )

    def source_retry_at(self, source: MetadataSource) -> datetime | None:
        # Conservative source-wide cooldown derived from durable transient rows.
        # No new table, synthetic cache identity or migration is necessary.
        with self.engine.connect() as connection:
            result = connection.scalar(
                text("""
                SELECT max(greatest(
                    CASE WHEN status='transient_error' THEN next_retry_at END,
                    (payload->>'_source_retry_at')::timestamptz
                )) FROM metadata_cache WHERE source=:source
            """),
                {"source": source},
            )
            return cast(datetime | None, result)

    def reject(self, work: EnrichmentWork, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(EnrichmentQueueItem)
                .where(
                    EnrichmentQueueItem.entity_type == work.entity_type,
                    EnrichmentQueueItem.entity_mbid == UUID(work.mbid),
                )
                .values(
                    status="failed",
                    next_retry_at=None,
                    updated_at=now,
                    locked_at=None,
                    last_error="permanent entity lookup failure; manual retry required",
                )
            )

    def complete(
        self,
        work: EnrichmentWork,
        votes: list[GenreVote],
        country: str | None,
        next_retry_at: datetime,
        now: datetime,
    ) -> None:
        mbid = UUID(work.mbid)
        with Session(self.engine) as session, session.begin():
            existing = session.execute(
                select(EntityGenre.genre_mbid, EntityGenre.votes).where(
                    EntityGenre.entity_type == work.entity_type,
                    EntityGenre.entity_mbid == mbid,
                )
            ).all()
            changed = {str(g): count for g, count in existing} != {v.mbid: v.count for v in votes}
            for vote in votes:
                old_name = session.scalar(
                    select(Genre.name).where(Genre.genre_mbid == UUID(vote.mbid))
                )
                if old_name != vote.name:
                    changed = True
                session.execute(
                    insert(Genre)
                    .values(genre_mbid=UUID(vote.mbid), name=vote.name)
                    .on_conflict_do_update(index_elements=["genre_mbid"], set_={"name": vote.name})
                )
            if changed:
                session.execute(
                    delete(EntityGenre).where(
                        EntityGenre.entity_type == work.entity_type, EntityGenre.entity_mbid == mbid
                    )
                )
                for vote in votes:
                    session.add(
                        EntityGenre(
                            entity_type=work.entity_type,
                            entity_mbid=mbid,
                            genre_mbid=UUID(vote.mbid),
                            votes=vote.count,
                            fetched_at=now,
                        )
                    )
            if work.entity_type == "artist":
                old_country = session.get(ArtistCountry, mbid)
                if (old_country.country_code if old_country else None) != country:
                    changed = True
                    if old_country:
                        session.delete(old_country)
                        session.flush()
                    if country:
                        session.add(
                            ArtistCountry(artist_mbid=mbid, country_code=country, fetched_at=now)
                        )
            if changed:
                session.execute(
                    insert(DataGeneration)
                    .values(id=1, enrichment_generation=1, last_updated=now)
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "enrichment_generation": DataGeneration.enrichment_generation + 1,
                            "last_updated": now,
                        },
                    )
                )
            session.execute(
                update(EnrichmentQueueItem)
                .where(
                    EnrichmentQueueItem.entity_type == work.entity_type,
                    EnrichmentQueueItem.entity_mbid == mbid,
                )
                .values(
                    status="done",
                    attempts=0,
                    next_retry_at=next_retry_at,
                    locked_at=None,
                    updated_at=now,
                    last_error=None,
                )
            )

    def retry(self, work: EnrichmentWork, next_retry_at: datetime, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(EnrichmentQueueItem)
                .where(
                    EnrichmentQueueItem.entity_type == work.entity_type,
                    EnrichmentQueueItem.entity_mbid == UUID(work.mbid),
                )
                .values(
                    status="failed",
                    attempts=EnrichmentQueueItem.attempts + 1,
                    next_retry_at=next_retry_at,
                    locked_at=None,
                    updated_at=now,
                    last_error="upstream transient failure",
                )
            )
