"""Discovery plugin and enrichment composition root. Importing never performs I/O.

Worker-only environment:
MUSIC_FIXTURE_MODE=true (default) disables upstream access.
MUSIC_METADATA_USER_AGENT is required for non-fixture mode, e.g.
'music-dashboard/0.1 (mailto:operator@example.org)'.
MUSIC_METADATA_EMPTY_TTL_DAYS=7, MUSIC_METADATA_POSITIVE_TTL_DAYS=30,
MUSIC_METADATA_RETRY_BASE_SECONDS=30, MUSIC_METADATA_RETRY_MAX_SECONDS=86400,
MUSIC_ENRICHMENT_BATCH_SIZE=100 configure persistent scheduling.
Release/release-group title lookups (worker only, exact MBID, after genre enrichment):
MUSIC_TITLE_LOOKUP_BUDGET=25 max upstream requests per run,
MUSIC_TITLE_LOOKUP_DEADLINE_SECONDS=120 wall-clock cap per run (finite, >0).
Metadata endpoints are public: this plugin never reads a ListenBrainz token.
"""

import os
from datetime import timedelta


def run_once(limit: int = 100) -> int:
    # Concrete wiring lives in discovered plugins; shared bootstrap remains frozen.
    from sqlalchemy import text

    from music_dashboard.application.enrich_metadata import EnrichmentPolicy, EnrichMetadata
    from music_dashboard.application.resolve_titles import ResolveTitles, TitlePolicy
    from music_dashboard.infrastructure.listenbrainz.metadata import (
        ListenBrainzMetadataClient,
        ListenBrainzMetadataConfig,
    )
    from music_dashboard.infrastructure.musicbrainz.client import (
        MusicBrainzClient,
        MusicBrainzConfig,
    )
    from music_dashboard.infrastructure.musicbrainz.title_lookup import MusicBrainzTitleClient
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.metadata import (
        ENRICHMENT_LOCK,
        SqlMetadataRepository,
    )
    from music_dashboard.infrastructure.persistence.repositories.title_cache import (
        SqlTitleCacheRepository,
    )

    if os.environ.get("MUSIC_FIXTURE_MODE", "true").lower() not in {"false", "0", "no"}:
        raise ValueError(
            "Enrichment requires MUSIC_FIXTURE_MODE=false; fixture mode never calls HTTP"
        )
    user_agent = os.environ.get("MUSIC_METADATA_USER_AGENT", "")
    mb = MusicBrainzClient(MusicBrainzConfig(user_agent=user_agent))
    lb = ListenBrainzMetadataClient(ListenBrainzMetadataConfig(user_agent=user_agent))
    policy = EnrichmentPolicy(
        empty_ttl=timedelta(days=float(os.environ.get("MUSIC_METADATA_EMPTY_TTL_DAYS", "7"))),
        positive_ttl=timedelta(
            days=float(os.environ.get("MUSIC_METADATA_POSITIVE_TTL_DAYS", "30"))
        ),
        retry_base_seconds=float(os.environ.get("MUSIC_METADATA_RETRY_BASE_SECONDS", "30")),
        retry_max_seconds=float(os.environ.get("MUSIC_METADATA_RETRY_MAX_SECONDS", "86400")),
    )
    engine = create_db_engine()
    try:
        with engine.connect() as connection:
            locked = connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ENRICHMENT_LOCK}
            )
            connection.commit()
            if not locked:
                return 0
            try:
                done = EnrichMetadata(SqlMetadataRepository(engine), lb, mb, policy).run(limit)
                # Titles run under the same session lock, after genre enrichment; best-effort.
                ResolveTitles(
                    SqlTitleCacheRepository(engine),
                    MusicBrainzTitleClient(MusicBrainzConfig(user_agent=user_agent)),
                    TitlePolicy(
                        max_requests=int(os.environ.get("MUSIC_TITLE_LOOKUP_BUDGET", "25")),
                        deadline_seconds=float(
                            os.environ.get("MUSIC_TITLE_LOOKUP_DEADLINE_SECONDS", "120")
                        ),
                    ),
                ).run()
                return done
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": ENRICHMENT_LOCK}
                )
                connection.commit()
    finally:
        engine.dispose()


def tick() -> None:
    if os.environ.get("MUSIC_FIXTURE_MODE", "true").lower() in {"false", "0", "no"}:
        run_once(int(os.environ.get("MUSIC_ENRICHMENT_BATCH_SIZE", "100")))
