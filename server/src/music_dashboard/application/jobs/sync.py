"""Worker registry entry and sync composition. Imports are side-effect free.

Worker-only environment: LISTENBRAINZ_USERNAME/TOKEN, MUSIC_SYNC_OVERLAP_HOURS
(default 72), MUSIC_SYNC_INTERVAL_SECONDS (900), MUSIC_RECONCILE_INTERVAL_SECONDS
(604800). Configuration is deliberately not added to API Settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from music_dashboard.application.reconcile_history import reconcile_history
from music_dashboard.application.sync_listens import SyncBusy, sync_listens


class SyncConfigurationError(ValueError):
    """Safe, actionable configuration error; contains no environment values."""


@dataclass(frozen=True)
class WorkerSyncSettings:
    fixture_mode: bool
    user: str
    token: str = field(repr=False)
    overlap_hours: float = 72
    interval_seconds: int = 900
    reconcile_seconds: int = 604800

    @classmethod
    def from_env(cls) -> WorkerSyncSettings:
        fixture = os.environ.get("MUSIC_FIXTURE_MODE", "true").lower() in {"true", "1", "yes"}
        user = os.environ.get("LISTENBRAINZ_USERNAME", "").strip()
        token = os.environ.get("LISTENBRAINZ_TOKEN", "").strip()
        if not fixture and (not user or not token):
            raise SyncConfigurationError(
                "Set LISTENBRAINZ_USERNAME and LISTENBRAINZ_TOKEN privately in the worker "
                "environment, or set MUSIC_FIXTURE_MODE=true for offline fixtures"
            )
        try:
            overlap = float(os.environ.get("MUSIC_SYNC_OVERLAP_HOURS", "72"))
            interval = int(os.environ.get("MUSIC_SYNC_INTERVAL_SECONDS", "900"))
            reconcile = int(os.environ.get("MUSIC_RECONCILE_INTERVAL_SECONDS", "604800"))
        except ValueError:
            raise SyncConfigurationError("Sync overlap and schedule must be numeric") from None
        if not 0 < overlap < float("inf") or interval <= 0 or reconcile <= 0:
            raise SyncConfigurationError("Sync overlap and schedule intervals must be positive")
        return cls(fixture, user or "fixture-user", token, overlap, interval, reconcile)


def run_sync(
    kind: str, *, allow_large_delete: bool = False, scheduled: bool = False
) -> dict[str, Any]:
    # Composition belongs at the registry edge, not in inward use cases.
    from music_dashboard.infrastructure.listenbrainz.fixtures import FixtureListenBrainzClient
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )

    config = WorkerSyncSettings.from_env()
    if scheduled and config.fixture_mode:
        return {"status": "fixture-idle"}
    client: Any
    if config.fixture_mode:
        client = FixtureListenBrainzClient()
    else:
        from music_dashboard.infrastructure.listenbrainz.listens import ListenBrainzClient
        from music_dashboard.infrastructure.listenbrainz.transport import (
            ListenBrainzTransport,
            UrllibHttpTransport,
        )

        client = ListenBrainzClient(
            ListenBrainzTransport(UrllibHttpTransport(), token=config.token)
        )
    engine = create_db_engine()
    try:
        repository = SyncRepository(engine)
        now = datetime.now(UTC)
        # Read schedule while holding the same advisory lock as the use case.
        # A second acquisition is avoided by repository re-entrancy on this instance.
        with repository.locked(config.user) as acquired:
            if not acquired:
                raise SyncBusy("Another sync owns the user lock; retry later")
            if scheduled:
                state = repository.load_checkpoint(config.user)
                last_export = state.get("last_export_at")
                export_due = (
                    state.get("requires_reconcile")
                    or last_export is None
                    or now - last_export >= timedelta(seconds=config.reconcile_seconds)
                )
                export_retry = repository.next_retry(config.user, "reconcile")
                export_ready = export_retry is None or export_retry <= now
                if export_due and export_ready:
                    kind = "reconcile"
                elif state.get("requires_reconcile"):
                    # Only genuine API ambiguity blocks ingestion, not ordinary
                    # weekly export errors or the large-deletion guard.
                    return {"status": "backoff"}
                else:
                    last = state.get("last_incremental_at")
                    if last is not None and now - last < timedelta(seconds=config.interval_seconds):
                        return {"status": "not-due"}
                    retry = repository.next_retry(config.user, "incremental")
                    if retry is not None and retry > now:
                        return {"status": "backoff"}
                    kind = "incremental"
            if kind in {"bootstrap", "reconcile"}:
                return reconcile_history(
                    client,
                    repository,
                    config.user,
                    allow_large_delete=allow_large_delete,
                    now=now,
                    bootstrap=kind == "bootstrap",
                )
            return sync_listens(
                client,
                repository,
                config.user,
                now=now,
                overlap=timedelta(hours=config.overlap_hours),
            )
    finally:
        engine.dispose()


def tick() -> None:
    run_sync("incremental", scheduled=True)
