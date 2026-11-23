"""Worker registry entry and sync composition. Imports are side-effect free.

Worker-only environment: LISTENBRAINZ_USERNAME/TOKEN, MUSIC_SYNC_OVERLAP_HOURS
(default 72), MUSIC_RECONCILE_INTERVAL_SECONDS (604800), MUSIC_EXPORT_MAX_BYTES
(export download cap, default 64 MiB, allowed 1 MiB..512 MiB; the archive is held in
memory, so peak usage is roughly this value plus parsing overhead).
The sync tick only runs the weekly reconcile; incrementals come from refresh requests.
Configuration is deliberately not added to API Settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from music_dashboard.application.reconcile_history import reconcile_history
from music_dashboard.application.sync_listens import (
    BoundaryAmbiguity,
    SyncBusy,
    failure_stats,
    sync_listens,
)

DEFAULT_EXPORT_MAX_BYTES = 64 << 20
MIN_EXPORT_MAX_BYTES = 1 << 20
MAX_EXPORT_MAX_BYTES = 512 << 20


class SyncConfigurationError(ValueError):
    """Safe, actionable configuration error; contains no environment values."""


@dataclass(frozen=True)
class WorkerSyncSettings:
    fixture_mode: bool
    user: str
    token: str = field(repr=False)
    overlap_hours: float = 72
    reconcile_seconds: int = 604800
    export_max_bytes: int = DEFAULT_EXPORT_MAX_BYTES

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
            reconcile = int(os.environ.get("MUSIC_RECONCILE_INTERVAL_SECONDS", "604800"))
            export_cap = int(
                os.environ.get("MUSIC_EXPORT_MAX_BYTES", str(DEFAULT_EXPORT_MAX_BYTES))
            )
        except ValueError:
            raise SyncConfigurationError("Sync overlap and schedule must be numeric") from None
        if not 0 < overlap < float("inf") or reconcile <= 0:
            raise SyncConfigurationError("Sync overlap and schedule intervals must be positive")
        if not MIN_EXPORT_MAX_BYTES <= export_cap <= MAX_EXPORT_MAX_BYTES:
            raise SyncConfigurationError("MUSIC_EXPORT_MAX_BYTES is outside the allowed range")
        return cls(fixture, user or "fixture-user", token, overlap, reconcile, export_cap)


def _build_client(config: WorkerSyncSettings) -> Any:
    from music_dashboard.infrastructure.listenbrainz.fixtures import FixtureListenBrainzClient

    if config.fixture_mode:
        return FixtureListenBrainzClient()
    from music_dashboard.infrastructure.listenbrainz.listens import ListenBrainzClient
    from music_dashboard.infrastructure.listenbrainz.transport import (
        ListenBrainzTransport,
        UrllibHttpTransport,
    )

    return ListenBrainzClient(
        ListenBrainzTransport(
            UrllibHttpTransport(max_body_bytes=config.export_max_bytes), token=config.token
        ),
        max_zip_bytes=config.export_max_bytes,
    )


def run_sync(
    kind: str, *, allow_large_delete: bool = False, scheduled: bool = False
) -> dict[str, Any]:
    # Composition belongs at the registry edge, not in inward use cases.
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )

    config = WorkerSyncSettings.from_env()
    if scheduled and config.fixture_mode:
        return {"status": "fixture-idle"}
    client = _build_client(config)
    engine = create_db_engine()
    try:
        repository = SyncRepository(engine)
        now = datetime.now(UTC)
        # Read schedule while holding the same advisory lock as the use case.
        # A second acquisition is avoided by repository re-entrancy on this instance.
        with repository.locked(config.user) as acquired:
            if not acquired:
                raise SyncBusy("Another sync owns the user lock; retry later")
            rate_deadline = repository.rate_limit_until(config.user)
            if rate_deadline is not None and rate_deadline > now:
                if scheduled:
                    return {"status": "backoff"}
                raise SyncBusy("Upstream rate limit is still active; retry after its deadline")
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
                    # Incrementals are admitted via public_refresh_request(), not here.
                    return {"status": "not-due"}
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
    run_sync("reconcile", scheduled=True)


def run_public_refresh(now: datetime | None = None) -> dict[str, Any]:
    """Serve the durable public refresh request (incremental ONLY, never an export).

    Same gates as the scheduled tick, evaluated under the per-user advisory lock: upstream
    rate limit, per-kind retry deadline, `requires_reconcile` and the initial export
    (`last_export_at`) prerequisite. A closed gate defers the request (retried every tick
    until it expires); reconciliation is left to the normal schedule. A `running` row seen
    while holding the lock is an orphan of a dead worker and is recovered to pending.
    """
    from music_dashboard.infrastructure.persistence.database import create_db_engine
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )
    from music_dashboard.infrastructure.persistence.repositories.refresh import (
        RefreshRepository,
    )

    config = WorkerSyncSettings.from_env()
    engine = create_db_engine()
    try:
        refresh = RefreshRepository(engine)
        repository = SyncRepository(engine)
        now = now or datetime.now(UTC)
        if not refresh.has_active():
            return {"status": "idle"}
        with repository.locked(config.user) as acquired:
            if not acquired:
                return {"status": "busy"}  # scheduled sync owns the lock; retry next tick
            request = refresh.claim()
            if request is None:
                return {"status": "idle"}
            rid = request.request_id
            if config.fixture_mode:
                refresh.finish(rid, "failed", "fixture_mode")
                return {"status": "failed"}
            rate = repository.rate_limit_until(config.user)
            retry = repository.next_retry(config.user, "incremental")
            state = repository.load_checkpoint(config.user)
            if rate is not None and rate > now:
                refresh.mark_deferred(rid, "rate_limited", rate)
                return {"status": "deferred"}
            if retry is not None and retry > now:
                refresh.mark_deferred(rid, "retry_scheduled", retry)
                return {"status": "deferred"}
            if state.get("requires_reconcile"):
                refresh.mark_deferred(rid, "requires_reconcile")
                return {"status": "deferred"}
            if state.get("last_export_at") is None:
                refresh.mark_deferred(rid, "awaiting_initial_export")
                return {"status": "deferred"}
            if not refresh.mark_running(rid):
                return {"status": "superseded"}
            try:
                result = sync_listens(
                    _build_client(config),
                    repository,
                    config.user,
                    now=now,
                    overlap=timedelta(hours=config.overlap_hours),
                )
            except SyncBusy:
                refresh.mark_deferred(rid, "retry_scheduled")
                return {"status": "deferred"}
            except BoundaryAmbiguity:
                refresh.mark_deferred(rid, "requires_reconcile")
                return {"status": "deferred"}
            except Exception as exc:
                retry_at = failure_stats(exc, now=now)["next_retry_at"]
                refresh.finish(rid, "failed", "sync_failed", datetime.fromisoformat(retry_at))
                return {"status": "failed"}
            refresh.finish(rid, "succeeded")
            return {"status": "succeeded", **result}
    finally:
        engine.dispose()
