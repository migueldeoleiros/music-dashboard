import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from music_dashboard.infrastructure.persistence.models import Base
from music_dashboard.infrastructure.persistence.seed import seed_fixtures
from music_dashboard.infrastructure.persistence.seed_data import FIXTURE_USER, LISTENS
from music_dashboard.infrastructure.persistence.timezones import canonical_timezone, load_timezones

from .conftest import alembic_cfg

pytestmark = pytest.mark.integration


def scalar(eng, sql, **p):
    with eng.connect() as c:
        return c.execute(text(sql), p).scalar_one()


def test_two_step_upgrade_matches_models_and_downgrades(fresh_db, admin_url):
    cfg = alembic_cfg(admin_url)
    command.upgrade(cfg, "0001")
    assert scalar(fresh_db, "SELECT count(*) FROM timezones") == len(load_timezones())
    command.upgrade(cfg, "head")
    with fresh_db.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_timezones_seeded_and_valid_in_postgres(migrated_db):
    assert (
        scalar(migrated_db, "SELECT canonical_name FROM timezones WHERE name='US/Eastern'")
        == "America/New_York"
    )
    assert canonical_timezone("US/Eastern") == "America/New_York"
    bad = scalar(
        migrated_db,
        "SELECT count(*) FROM timezones t WHERE NOT EXISTS "
        "(SELECT 1 FROM pg_timezone_names p WHERE p.name = t.name)",
    )
    assert bad == 0, "controlled timezone not known to this PostgreSQL tzdata"


def test_seed_idempotent_and_edge_cases(migrated_db):
    first = seed_fixtures(migrated_db)
    assert seed_fixtures(migrated_db) == first
    q = lambda s: scalar(migrated_db, s)  # noqa: E731
    assert q("SELECT count(*) FROM listens") == len(LISTENS)
    assert (
        q("SELECT max(c) FROM (SELECT count(*) c FROM listens GROUP BY listened_at) s") >= 3
    )  # same second
    assert q("SELECT count(*) FROM listens WHERE release_name IS NULL") >= 1  # no album
    assert q("SELECT count(*) FROM listens WHERE recording_mbid IS NULL") >= 1  # unmapped
    assert (
        q("SELECT count(*) FROM listen_artists GROUP BY listen_id ORDER BY 1 DESC LIMIT 1") >= 2
    )  # multi-credit
    assert q("SELECT count(*) FROM entity_genres WHERE votes = 0") >= 1  # no positives
    assert q("SELECT count(*) FROM entity_genres WHERE votes < 0") >= 1  # mixed vote signs
    assert (
        q("SELECT (TIMESTAMPTZ '2011-12-30 10:00Z' AT TIME ZONE 'Pacific/Apia')::date::text")
        == "2011-12-31"
    )
    assert q("SELECT count(DISTINCT entity_type) FROM entity_genres") == 3  # each fallback level
    assert q("SELECT count(*) FROM artist_countries WHERE country_code IN ('XW','XE')") == 2
    # DST: Chisinau spring-forward skips 03:00-03:59 local
    assert (
        q("SELECT (TIMESTAMPTZ '2026-03-29 01:00Z' AT TIME ZONE 'Europe/Chisinau')::text")
        == "2026-03-29 04:00:00"
    )
    # fall-back: New York 01:30 local occurs twice
    assert (
        q(
            "SELECT count(DISTINCT listened_at AT TIME ZONE 'America/New_York') FROM listens "
            "WHERE listened_at IN ('2026-11-01 05:30Z','2026-11-01 06:30Z')"
        )
        == 1
    )
    assert (
        q("SELECT (TIMESTAMPTZ '2026-01-15 18:30Z' AT TIME ZONE 'Asia/Kolkata')::text")
        == "2026-01-16 00:00:00"
    )
    assert q(f"SELECT count(*) FROM raw_listens WHERE user_name='{FIXTURE_USER}'") == len(LISTENS)


def test_api_role_least_privilege(migrated_db, api_url):
    seed_fixtures(migrated_db)
    # Disable the read-only default: privileges must deny writes independently of settings.
    api = create_engine(api_url, connect_args={"options": "-c default_transaction_read_only=off"})
    try:
        assert scalar(api, "SHOW default_transaction_read_only") == "off"
        assert scalar(api, "SELECT count(*) FROM listens") == len(LISTENS)
        for table in (
            "listen_artists",
            "genres",
            "entity_genres",
            "artist_countries",
            "data_generation",
        ):
            assert scalar(api, f"SELECT count(*) FROM {table}") >= 0
        assert scalar(api, "SELECT count(*) FROM timezones") > 0
        assert scalar(api, "SHOW statement_timeout") == "5s"
        for t in (
            "raw_listens",
            "quarantined_listens",
            "sync_runs",
            "sync_state",
            "metadata_cache",
            "enrichment_queue",
            "alembic_version",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                scalar(api, f"SELECT count(*) FROM {t}")
        for stmt in (
            "INSERT INTO genres VALUES (gen_random_uuid(), 'x')",
            "DELETE FROM listens",
            "UPDATE data_generation SET ingestion_generation = 0",
            "CREATE TABLE public.x (i int)",
        ):
            with pytest.raises(DBAPIError):
                with api.begin() as c:
                    c.execute(text(stmt))
        with pytest.raises(DBAPIError, match="timeout"):
            with api.begin() as c:
                c.execute(text("SELECT pg_sleep(6)"))
    finally:
        api.dispose()


def test_roles_not_privileged(migrated_db):
    with migrated_db.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles "
                "WHERE rolname IN ('music_writer','music_api')"
            )
        ).all()
    assert {r[0] for r in row} == {"music_writer", "music_api"}
    # writer must not be a superuser in deployment (compose/init-db.sh); test clusters may
    # reuse it as admin, so only assert for music_api here and writer when separate.
    for name, sup, crole, cdb in row:
        if name == "music_api":
            assert not (sup or crole or cdb)
    assert (
        scalar(migrated_db, "SELECT has_table_privilege('music_api','listens','INSERT')") is False
    )
    assert (
        scalar(migrated_db, "SELECT has_table_privilege('music_api','raw_listens','SELECT')")
        is False
    )
    assert (
        scalar(migrated_db, "SELECT has_table_privilege('music_writer','raw_listens','INSERT')")
        is True
    )


def test_migrate_and_seed_as_non_superuser_writer(fresh_db):
    import os

    writer_url = os.environ.get("TEST_WRITER_DATABASE_URL")
    if not writer_url:
        pytest.skip("TEST_WRITER_DATABASE_URL not set")
    with fresh_db.connect() as c:
        c.execute(text("ALTER SCHEMA public OWNER TO music_writer"))
    writer = create_engine(writer_url)
    try:
        assert scalar(writer, "SELECT rolsuper FROM pg_roles WHERE rolname=current_user") is False
        command.upgrade(alembic_cfg(writer_url), "0001")
        command.upgrade(alembic_cfg(writer_url), "head")
        assert seed_fixtures(writer)["listens"] == len(LISTENS)
        command.downgrade(alembic_cfg(writer_url), "base")
        command.upgrade(alembic_cfg(writer_url), "head")
    finally:
        writer.dispose()
