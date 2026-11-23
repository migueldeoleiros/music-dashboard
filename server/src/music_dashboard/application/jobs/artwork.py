"""Artwork job plugin: tick() resolves a bounded batch of image URLs. Import is side-effect free.

Worker-only environment (same fixture switch as enrichment: MUSIC_FIXTURE_MODE=false enables it):
MUSIC_METADATA_USER_AGENT (required, descriptive contact), MUSIC_ARTWORK_BATCH_SIZE=20,
MUSIC_ARTWORK_BUSY_BATCH_SIZE=5 / MUSIC_ARTWORK_BUSY_EVERY=4 (reduced share while the genre
enrichment queue has due work), MUSIC_ARTWORK_POSITIVE_TTL_DAYS=30, MUSIC_ARTWORK_MISS_TTL_DAYS=7,
MUSIC_ARTWORK_RETRY_BASE_SECONDS=60, MUSIC_ARTWORK_RETRY_MAX_SECONDS=86400.
WebP copies: MUSIC_ARTWORK_DIR (shared volume; unset = disabled),
MUSIC_ARTWORK_CONVERT_BATCH_SIZE=10.
Metadata/artwork endpoints are public: this job never reads a ListenBrainz token.
"""

import os
from datetime import UTC, datetime, timedelta

from music_dashboard.application.resolve_artwork import QuotaGate

# In-process state: the worker runs ticks sequentially in one process.
_quota = QuotaGate()
_ticks = 0


def _fixture_mode() -> bool:
    return os.environ.get("MUSIC_FIXTURE_MODE", "true").lower() not in {"false", "0", "no"}


def run_once(limit: int = 20, *, reset_retries: bool = False) -> dict[str, int]:
    from sqlalchemy import text

    from music_dashboard.application.resolve_artwork import ResolveArtwork
    from music_dashboard.domain.artwork.policies import ArtworkPolicy
    from music_dashboard.infrastructure.artwork.caa import CoverArtArchiveClient
    from music_dashboard.infrastructure.artwork.deezer import DeezerClient
    from music_dashboard.infrastructure.artwork.wikidata import WikidataClient
    from music_dashboard.infrastructure.musicbrainz.artwork_lookup import (
        MusicBrainzArtworkLookups,
    )
    from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.artwork import (
        ARTWORK_LOCK,
        SqlArtworkRepository,
    )

    if _fixture_mode():
        raise ValueError("Artwork requires MUSIC_FIXTURE_MODE=false; fixture mode never calls HTTP")
    user_agent = os.environ.get("MUSIC_METADATA_USER_AGENT", "")
    config = MusicBrainzConfig(user_agent=user_agent)  # validates the descriptive contact UA
    env = os.environ.get
    policy = ArtworkPolicy(
        positive_ttl=timedelta(days=float(env("MUSIC_ARTWORK_POSITIVE_TTL_DAYS", "30"))),
        miss_ttl=timedelta(days=float(env("MUSIC_ARTWORK_MISS_TTL_DAYS", "7"))),
        retry_base_seconds=float(env("MUSIC_ARTWORK_RETRY_BASE_SECONDS", "60")),
        retry_max_seconds=float(env("MUSIC_ARTWORK_RETRY_MAX_SECONDS", "86400")),
    )
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            locked = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ARTWORK_LOCK}
            )
            connection.commit()
            if not locked:
                return {"resolved": 0, "miss": 0, "transient": 0, "quota": 0}
            try:
                repository = SqlArtworkRepository(engine)
                if reset_retries:
                    repository.reset_retries()
                return ResolveArtwork(
                    repository,
                    MusicBrainzArtworkLookups(config),
                    DeezerClient(user_agent),
                    CoverArtArchiveClient(user_agent),
                    WikidataClient(user_agent),
                    policy,
                    quota=_quota,
                ).run(limit)
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ARTWORK_LOCK})
                connection.commit()
    finally:
        engine.dispose()


def _busy(now: datetime) -> bool:
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.artwork import (
        SqlArtworkRepository,
    )

    engine = create_db_engine()
    try:
        return SqlArtworkRepository(engine).enrichment_has_due_work(now)
    finally:
        engine.dispose()


_failed: dict[str, datetime] = {}


def convert_pending(limit: int = 10) -> int:
    """WebP-convert resolved artwork lacking a local copy (needs MUSIC_ARTWORK_DIR).

    Bumps the data generation when anything converted so cached responses switch to local URLs.
    """
    from music_dashboard.infrastructure.artwork import store
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.artwork import (
        SqlArtworkRepository,
    )

    directory = store.artwork_dir()
    if directory is None:
        return 0
    engine = create_db_engine()
    try:
        repository = SqlArtworkRepository(engine)
        done = store.convert_missing(
            directory,
            repository.resolved_urls(),
            store.user_agent_downloader(os.environ.get("MUSIC_METADATA_USER_AGENT", "")),
            _failed,
            datetime.now(UTC),
            limit,
        )
        if done:
            repository.bump_generation()
        return done
    finally:
        engine.dispose()


def tick() -> None:
    """Idle enrichment queue: full batch. Busy queue: small batch on every Nth tick only."""
    global _ticks
    if _fixture_mode():
        return
    _ticks += 1
    batch = int(os.environ.get("MUSIC_ARTWORK_BATCH_SIZE", "20"))
    if _busy(datetime.now(UTC)):
        every = max(1, int(os.environ.get("MUSIC_ARTWORK_BUSY_EVERY", "4")))
        if _ticks % every:
            return
        batch = min(batch, int(os.environ.get("MUSIC_ARTWORK_BUSY_BATCH_SIZE", "5")))
    run_once(max(1, min(batch, 100)))
    convert_pending(int(os.environ.get("MUSIC_ARTWORK_CONVERT_BATCH_SIZE", "10")))
