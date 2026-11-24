"""Recording durations projection (listening minutes)

`recording_durations` holds one track length per recording MBID, projected by the worker from
the ListenBrainz recording metadata (`recording.length`, milliseconds). It is backfilled here from
the already cached payloads, so no upstream call is needed. The API role gets SELECT only.
Listens without a known length use the average of the known ones at read time.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_durations",
        sa.Column("recording_mbid", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("recording_mbid", name="pk_recording_durations"),
        sa.CheckConstraint("duration_ms > 0", name="ck_recording_durations_positive"),
    )
    op.execute(
        """
        INSERT INTO recording_durations (recording_mbid, duration_ms)
        SELECT entity_mbid, (payload #>> '{metadata,recording,length}')::integer
        FROM metadata_cache
        WHERE entity_type = 'recording' AND source = 'listenbrainz'
          AND payload #>> '{metadata,recording,length}' ~ '^[0-9]{1,9}$'
          AND (payload #>> '{metadata,recording,length}')::integer > 0
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("REVOKE ALL ON recording_durations FROM PUBLIC, music_api")
    op.execute("GRANT SELECT ON recording_durations TO music_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON recording_durations TO music_writer")


def downgrade() -> None:
    op.drop_table("recording_durations")
