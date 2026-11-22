"""Bounded artist lookup and chronological history keyset indexes.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_listens_history_keyset", "listens", [sa.text("listened_at DESC"), sa.text("id DESC")]
    )
    op.create_index(
        "ix_listen_artists_artist_listen", "listen_artists", ["artist_mbid", "listen_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_listen_artists_artist_listen", table_name="listen_artists")
    op.drop_index("ix_listens_history_keyset", table_name="listens")
