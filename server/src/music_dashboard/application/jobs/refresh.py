"""Worker tick for refresh: hourly auto-admission, then serve any durable request.

Registered before `sync` (alphabetical), so a requested incremental runs ahead of the
weekly reconcile within the same serial tick. Never starts an export.

Auto-admission calls the same DB function as `POST /api/refresh` (no HTTP); the 10 min
cooldown in that function decides whether a request is actually admitted. The timer is
in-process and monotonic and fires at startup (restart-safe thanks to the cooldown).
Env: MUSIC_REFRESH_AUTO_INTERVAL_SECONDS (default 3600). Skipped in fixture mode.
"""

import logging
import os
import time

from music_dashboard.application.jobs.sync import run_public_refresh

DEFAULT_AUTO_INTERVAL_SECONDS = 3600
_next_auto: float | None = None  # monotonic deadline; None = fire now (startup)


def _fixture_mode() -> bool:
    return os.environ.get("MUSIC_FIXTURE_MODE", "true").lower() in {"true", "1", "yes"}


def _interval() -> int:
    try:
        value = int(os.environ.get("MUSIC_REFRESH_AUTO_INTERVAL_SECONDS", ""))
    except ValueError:
        return DEFAULT_AUTO_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_AUTO_INTERVAL_SECONDS


def auto_admit() -> str:
    """Admit a refresh when the hourly timer is due. Returns an outcome code."""
    global _next_auto
    if _fixture_mode():
        return "fixture-idle"
    now = time.monotonic()
    if _next_auto is not None and now < _next_auto:
        return "not-due"
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.refresh import (
        RefreshRepository,
    )

    engine = create_db_engine()
    try:
        status, coalesced = RefreshRepository(engine).admit()
    except Exception:
        # Timer not advanced: retry next tick. No exception text or payload is logged.
        logging.error("Auto refresh admission failed; retry on next tick")
        return "error"
    finally:
        engine.dispose()
    _next_auto = now + _interval()
    logging.info("Auto refresh admission: status=%s coalesced=%s", status, coalesced)
    return "coalesced" if coalesced else status


def tick() -> None:
    auto_admit()
    result = run_public_refresh()
    if result.get("status") not in {"idle", "busy"}:
        logging.info("Public refresh: status=%s", result.get("status"))
