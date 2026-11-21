"""Alembic env. URL: config.attributes['url'] > MUSIC_DATABASE_URL. Run as the owner role."""

from alembic import context
from sqlalchemy import create_engine, pool

from music_dashboard.infrastructure.persistence.database import resolve_url
from music_dashboard.infrastructure.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return resolve_url(config.attributes.get("url"))


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
