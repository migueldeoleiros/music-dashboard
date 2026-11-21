"""artwork provenance + name-only artwork (Koito-style name fallbacks)

Adds writer-only provenance columns to `artwork` (match_kind, provider_ref) and a new
`artwork_name` table keyed by the stable ranking key (`name:...`) for ranking entities that have
no MBID. Both store https image URLs only. Name matches never rewrite listens, never change a
ranking key and never merge identities: they only attach an image URL to an existing key.

The API role gets column-level SELECT on artwork_name (entity_type, name_key, image_url, status)
only; provenance, source and retry columns stay writer-only, like on `artwork`.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
API_COLUMNS = ("entity_type", "name_key", "image_url", "status")
NAME_KINDS = "'artist_name','artist_alias','album_title_artist','album_title','track_title_artist'"
URL_CHECK = "image_url IS NULL OR image_url ~ '^https://[^[:space:]]+$'"


def upgrade() -> None:
    op.add_column("artwork", sa.Column("match_kind", sa.Text, nullable=False, server_default="id"))
    op.add_column("artwork", sa.Column("provider_ref", sa.Text))
    op.create_check_constraint(
        "ck_artwork_match_kind",
        "artwork",
        f"match_kind IN ('id',{NAME_KINDS}) AND (match_kind = 'id' OR "
        "(status = 'resolved' AND source = 'deezer' AND provider_ref IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_artwork_provider_ref",
        "artwork",
        "provider_ref IS NULL OR provider_ref ~ '^[1-9][0-9]{0,15}$'",
    )
    op.create_table(
        "artwork_name",
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("name_key", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("image_url", sa.Text),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("match_kind", sa.Text, nullable=False, server_default="none"),
        sa.Column("provider_ref", sa.Text),
        sa.Column("fetched_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("next_retry_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("entity_type", "name_key", name="pk_artwork_name"),
        sa.CheckConstraint(
            "entity_type IN ('album','track','artist')", name="ck_artwork_name_entity_type"
        ),
        sa.CheckConstraint(
            "name_key LIKE 'name:%' AND length(name_key) <= 1000", name="ck_artwork_name_key"
        ),
        sa.CheckConstraint("source IN ('deezer','none')", name="ck_artwork_name_source"),
        sa.CheckConstraint(
            "status IN ('resolved','miss','transient')", name="ck_artwork_name_status"
        ),
        sa.CheckConstraint(URL_CHECK, name="ck_artwork_name_image_url"),
        sa.CheckConstraint(
            "(status = 'resolved') = (image_url IS NOT NULL) "
            "AND (status = 'resolved') = (source <> 'none')",
            name="ck_artwork_name_resolved_has_url",
        ),
        sa.CheckConstraint(
            f"((status = 'resolved') = (match_kind IN ({NAME_KINDS}))) "
            "AND ((status = 'resolved') = (provider_ref IS NOT NULL)) "
            "AND (provider_ref IS NULL OR provider_ref ~ '^[1-9][0-9]{0,15}$')",
            name="ck_artwork_name_provenance",
        ),
    )
    op.create_index(
        "ix_artwork_name_next_retry_at",
        "artwork_name",
        ["next_retry_at"],
        postgresql_where=sa.text("next_retry_at IS NOT NULL"),
    )
    op.execute("REVOKE ALL ON artwork_name FROM PUBLIC, music_api")
    op.execute(f"GRANT SELECT ({', '.join(API_COLUMNS)}) ON artwork_name TO music_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON artwork_name TO music_writer")


def downgrade() -> None:
    op.drop_index("ix_artwork_name_next_retry_at", table_name="artwork_name")
    op.drop_table("artwork_name")
    # Dropping the columns drops the check constraints that reference them.
    op.drop_column("artwork", "provider_ref")
    op.drop_column("artwork", "match_kind")
