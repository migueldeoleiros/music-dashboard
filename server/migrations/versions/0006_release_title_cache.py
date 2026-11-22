"""MusicBrainz release / release-group title cache + partial listens(release_mbid) index

`release_title_cache` is the positive projection (verified display titles, worker-fetched by
exact MBID); `release_title_lookup` is writer-only retry/negative state. No cover/image data
lives here. The API role gets column-level SELECT of (entity_type, entity_mbid, title) only;
lookup state has no API grant. Stored listen titles and conflicts always win at read time: the
cache only fills labels that listens do not carry. Nothing here writes to listens.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
API_COLUMNS = ("entity_type", "entity_mbid", "title")


def upgrade() -> None:
    op.create_table(
        "release_title_cache",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_release_title_cache"),
        sa.CheckConstraint(
            "entity_type IN ('release','release_group')", name="ck_release_title_cache_type"
        ),
        sa.CheckConstraint(
            "btrim(title) <> '' AND length(title) <= 1000", name="ck_release_title_cache_title"
        ),
    )
    op.create_table(
        "release_title_lookup",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("release_group_mbid", pg.UUID(as_uuid=True)),
        sa.Column("fetched_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("next_retry_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_release_title_lookup"),
        sa.CheckConstraint(
            "entity_type IN ('release','release_group')", name="ck_release_title_lookup_type"
        ),
        sa.CheckConstraint(
            "status IN ('resolved','not_found','transient')", name="ck_release_title_lookup_status"
        ),
        sa.CheckConstraint(
            "release_group_mbid IS NULL OR entity_type = 'release'",
            name="ck_release_title_lookup_rg_only_release",
        ),
    )
    op.create_index(
        "ix_release_title_lookup_next_retry_at",
        "release_title_lookup",
        ["next_retry_at"],
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )
    op.create_index(
        "ix_listens_release_mbid",
        "listens",
        ["release_mbid"],
        postgresql_where=sa.text("release_mbid IS NOT NULL"),
    )
    op.execute("REVOKE ALL ON release_title_cache, release_title_lookup FROM PUBLIC, music_api")
    op.execute(f"GRANT SELECT ({', '.join(API_COLUMNS)}) ON release_title_cache TO music_api")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON release_title_cache, release_title_lookup "
        "TO music_writer"
    )


def downgrade() -> None:
    op.drop_index("ix_listens_release_mbid", table_name="listens")
    op.drop_index("ix_release_title_lookup_next_retry_at", table_name="release_title_lookup")
    op.drop_table("release_title_lookup")
    op.drop_table("release_title_cache")
