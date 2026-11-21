"""Offline projection rebuild composition. No HTTP client is constructed or imported."""

from typing import Any


def run_once(*, dry_run: bool = False) -> dict[str, Any]:
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.rebuild import RebuildRepository

    engine = create_db_engine()
    try:
        return RebuildRepository(engine).rebuild(dry_run=dry_run)
    finally:
        engine.dispose()
