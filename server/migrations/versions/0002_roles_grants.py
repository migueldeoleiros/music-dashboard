"""least-privilege grants for music_writer / music_api

PRECONDITION: roles `music_writer` and `music_api` already exist (deploy/init-db.sh
bootstraps them; tests create them) and music_writer owns the database/schema. Migration
runs as music_writer (NOSUPERUSER); it never ALTERs roles. API role settings
(statement_timeout, read-only default) are set by init-db.sh only. Missing roles fail loudly.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Explicit approved API query surface. Excludes raw_listens, quarantined_listens,
# sync_*, metadata_cache, enrichment_queue, alembic_version.
API_TABLES = [
    "listens",
    "listen_artists",
    "genres",
    "entity_genres",
    "artist_countries",
    "data_generation",
    "timezones",
]
WRITER_TABLES = API_TABLES + [
    "raw_listens",
    "quarantined_listens",
    "sync_runs",
    "sync_state",
    "metadata_cache",
    "enrichment_queue",
]


def upgrade() -> None:
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'music_writer')
         OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'music_api') THEN
        RAISE EXCEPTION 'roles music_writer and music_api must exist '
                         'before migration 0002 (see deploy/init-db.sh)';
      END IF;
      EXECUTE format('GRANT CONNECT ON DATABASE %I TO music_writer, music_api', current_database());
    END $$""")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO music_writer, music_api")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, music_api")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, music_api")
    op.execute(f"GRANT SELECT ON {', '.join(API_TABLES)} TO music_api")
    # writer may be the owner (already all-powerful); grants are harmless and cover non-owner use
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(WRITER_TABLES)} TO music_writer"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO music_writer")


def downgrade() -> None:
    # never revoke from the owner (music_writer): it owns alembic_version too
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM music_api")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM music_api")
