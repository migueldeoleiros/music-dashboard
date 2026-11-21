"""artwork: resolved cover/picture URLs keyed by exact MBID (no name search)

Stores final https URLs only (never image bytes). The API role may read just the columns
needed to join image_url; attempts/timestamps/source stay writer-only.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
API_COLUMNS = ("entity_type", "entity_mbid", "image_url", "status")


def upgrade() -> None:
    op.create_table(
        "artwork",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("image_url", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("next_retry_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_artwork"),
        sa.CheckConstraint(
            "entity_type IN ('album','track','artist')", name="ck_artwork_entity_type"
        ),
        sa.CheckConstraint("source IN ('deezer','caa','none')", name="ck_artwork_source"),
        sa.CheckConstraint("status IN ('resolved','miss','transient')", name="ck_artwork_status"),
        sa.CheckConstraint(
            "image_url IS NULL OR image_url ~ '^https://[^[:space:]]+$'",
            name="ck_artwork_image_url",
        ),
        sa.CheckConstraint(
            "(status = 'resolved') = (image_url IS NOT NULL AND source <> 'none')",
            name="ck_artwork_resolved_has_url",
        ),
    )
    op.create_index(
        "ix_artwork_next_retry_at",
        "artwork",
        ["next_retry_at"],
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )
    # Explicit grants: new tables never inherit API access.
    op.execute("REVOKE ALL ON artwork FROM PUBLIC, music_api")
    op.execute(f"GRANT SELECT ({', '.join(API_COLUMNS)}) ON artwork TO music_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON artwork TO music_writer")


def downgrade() -> None:
    op.drop_index("ix_artwork_next_retry_at", table_name="artwork")
    op.drop_table("artwork")
