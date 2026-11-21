"""core schema: raw/quarantine, listens, sync, metadata cache/queue, genres, countries, timezones

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from music_dashboard.infrastructure.persistence.timezones import load_timezones

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.text("now()")
ENT = "entity_type IN ('recording','release_group','artist')"


def _id() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "raw_listens",
        _id(),
        sa.Column("user_name", sa.Text, nullable=False),
        sa.Column("listened_at", TS, nullable=False),
        sa.Column("recording_msid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("provenance", pg.JSONB, nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=NOW),
        sa.Column("tombstoned_at", TS),
        sa.Column("tombstone_reason", sa.Text),
        sa.UniqueConstraint(
            "user_name",
            "listened_at",
            "recording_msid",
            name="uq_raw_listens_user_name_listened_at_recording_msid",
        ),
    )
    op.create_index(
        "ix_raw_listens_active_listened_at",
        "raw_listens",
        ["user_name", "listened_at"],
        postgresql_where=sa.text("tombstoned_at IS NULL"),
    )

    op.create_table(
        "quarantined_listens",
        _id(),
        sa.Column("user_name", sa.Text),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "missing_fields", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("provenance", pg.JSONB, nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=NOW),
        sa.Column("resolved_at", TS),
    )

    op.create_table(
        "sync_runs",
        _id(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("user_name", sa.Text, nullable=False),
        sa.Column("started_at", TS, nullable=False, server_default=NOW),
        sa.Column("finished_at", TS),
        sa.Column("stats", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text),
        sa.CheckConstraint(
            "kind IN ('incremental','export','reconcile')", name="ck_sync_runs_kind"
        ),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','ambiguous','aborted')",
            name="ck_sync_runs_status",
        ),
    )
    op.create_table(
        "sync_state",
        sa.Column("user_name", sa.Text, primary_key=True),
        sa.Column("cursor_max_ts", sa.BigInteger),
        sa.Column("last_incremental_at", TS),
        sa.Column("last_export_at", TS),
        sa.Column("ambiguous_boundary_ts", sa.BigInteger),
        sa.Column(
            "requires_reconcile", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
    )

    op.create_table(
        "listens",
        _id(),
        sa.Column("user_name", sa.Text, nullable=False),
        sa.Column("listened_at", TS, nullable=False),
        sa.Column("recording_msid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("track_name", sa.Text),
        sa.Column("artist_name", sa.Text),
        sa.Column("release_name", sa.Text),
        sa.Column("recording_mbid", pg.UUID(as_uuid=True)),
        sa.Column("release_mbid", pg.UUID(as_uuid=True)),
        sa.Column("release_group_mbid", pg.UUID(as_uuid=True)),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "user_name",
            "listened_at",
            "recording_msid",
            name="uq_listens_user_name_listened_at_recording_msid",
        ),
    )
    op.create_index("ix_listens_listened_at", "listens", ["listened_at"])
    op.create_index(
        "ix_listens_recording_mbid",
        "listens",
        ["recording_mbid"],
        postgresql_where=sa.text("recording_mbid IS NOT NULL"),
    )
    op.create_index(
        "ix_listens_release_group_mbid",
        "listens",
        ["release_group_mbid"],
        postgresql_where=sa.text("release_group_mbid IS NOT NULL"),
    )
    op.create_table(
        "listen_artists",
        sa.Column("listen_id", sa.BigInteger, nullable=False),
        sa.Column("position", sa.SmallInteger, nullable=False),
        sa.Column("artist_name", sa.Text, nullable=False),
        sa.Column("artist_mbid", pg.UUID(as_uuid=True)),
        sa.PrimaryKeyConstraint("listen_id", "position", name="pk_listen_artists"),
        sa.ForeignKeyConstraint(
            ["listen_id"],
            ["listens.id"],
            name="fk_listen_artists_listen_id_listens",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_listen_artists_artist_mbid",
        "listen_artists",
        ["artist_mbid"],
        postgresql_where=sa.text("artist_mbid IS NOT NULL"),
    )

    op.create_table(
        "metadata_cache",
        _id(),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB),
        sa.Column("fetched_at", TS, nullable=False, server_default=NOW),
        sa.Column("next_retry_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text),
        sa.UniqueConstraint(
            "entity_type",
            "entity_mbid",
            "source",
            name="uq_metadata_cache_entity_type_entity_mbid_source",
        ),
        sa.CheckConstraint(ENT, name="ck_metadata_cache_entity_type"),
        sa.CheckConstraint(
            "source IN ('listenbrainz','musicbrainz')", name="ck_metadata_cache_source"
        ),
        sa.CheckConstraint(
            "status IN ('pending','positive','empty','transient_error')",
            name="ck_metadata_cache_status",
        ),
    )
    op.create_index(
        "ix_metadata_cache_next_retry_at",
        "metadata_cache",
        ["next_retry_at"],
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )
    op.create_table(
        "enrichment_queue",
        _id(),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", TS),
        sa.Column("locked_at", TS),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "entity_type", "entity_mbid", name="uq_enrichment_queue_entity_type_entity_mbid"
        ),
        sa.CheckConstraint(ENT, name="ck_enrichment_queue_entity_type"),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','done','failed')", name="ck_enrichment_queue_status"
        ),
    )
    op.create_index(
        "ix_enrichment_queue_pick", "enrichment_queue", ["status", "next_retry_at", "priority"]
    )

    op.create_table(
        "genres",
        sa.Column("genre_mbid", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
    )
    op.create_table(
        "entity_genres",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("genre_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("votes", sa.Integer, nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint(
            "entity_type", "entity_mbid", "genre_mbid", name="pk_entity_genres"
        ),
        sa.ForeignKeyConstraint(
            ["genre_mbid"], ["genres.genre_mbid"], name="fk_entity_genres_genre_mbid_genres"
        ),
        sa.CheckConstraint(ENT, name="ck_entity_genres_entity_type"),
    )
    op.create_table(
        "artist_countries",
        sa.Column("artist_mbid", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.Text, nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_artist_countries_country_code"),
    )
    op.create_table(
        "data_generation",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default=sa.text("1")),
        sa.Column(
            "ingestion_generation", sa.BigInteger, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "enrichment_generation", sa.BigInteger, nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_updated", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint("id = 1", name="ck_data_generation_singleton"),
    )
    op.execute("INSERT INTO data_generation (id) VALUES (1)")

    tz = op.create_table(
        "timezones",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(
            ["canonical_name"], ["timezones.name"], name="fk_timezones_canonical_name_timezones"
        ),
    )
    zones = load_timezones()
    # canonical rows first so the self-referencing FK is satisfied
    op.bulk_insert(
        tz, [{"name": n, "canonical_name": c} for n, c in sorted(zones.items()) if n == c]
    )
    op.bulk_insert(
        tz, [{"name": n, "canonical_name": c} for n, c in sorted(zones.items()) if n != c]
    )


def downgrade() -> None:
    for t in (
        "timezones",
        "data_generation",
        "artist_countries",
        "entity_genres",
        "genres",
        "enrichment_queue",
        "metadata_cache",
        "listen_artists",
        "listens",
        "sync_state",
        "sync_runs",
        "quarantined_listens",
        "raw_listens",
    ):
        op.drop_table(t)
