"""Env: TEST_DATABASE_URL (superuser/owner, disposable DB), TEST_API_DATABASE_URL (music_api role,
same DB). Optional TEST_WRITER_DATABASE_URL (music_writer). Roles are created if missing, with
the passwords in those URLs. The DB's public schema is DESTROYED on each test session."""

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from music_dashboard.infrastructure.persistence.database import resolve_url

SERVER = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _need(name):
    v = os.environ.get(name)
    if not v:
        pytest.skip(f"{name} not set")
    return resolve_url(v)


def alembic_cfg(url):
    cfg = Config(os.path.join(SERVER, "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


def _ensure_role(conn, role, url):
    pw = make_url(url).password or "test-" + role
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}).first()
    if exists and role == "music_writer":
        return  # may be the admin role itself; never change its password
    verb = "ALTER" if exists else "CREATE"
    conn.execute(text(f"{verb} ROLE {role} LOGIN PASSWORD '{pw.replace(chr(39), chr(39) * 2)}'"))
    if role == "music_api":  # mirror deploy/init-db.sh settings
        conn.execute(text("ALTER ROLE music_api SET statement_timeout = '5s'"))
        conn.execute(text("ALTER ROLE music_api SET default_transaction_read_only = on"))


@pytest.fixture(scope="session")
def admin_url():
    return _need("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def api_url():
    return _need("TEST_API_DATABASE_URL")


@pytest.fixture
def fresh_db(admin_url, api_url):
    """Empty public schema, roles present, no migrations applied."""
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        _ensure_role(c, "music_api", api_url)
        _ensure_role(
            c,
            "music_writer",
            os.environ.get("TEST_WRITER_DATABASE_URL") or "postgresql://x:wr-test@h/d",
        )
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    yield eng
    eng.dispose()


@pytest.fixture
def migrated_db(fresh_db, admin_url):
    command.upgrade(alembic_cfg(admin_url), "head")
    return fresh_db
