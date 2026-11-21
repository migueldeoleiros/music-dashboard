"""Identity-safe newest-first paging with durable, cleared-boundary checkpoints."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from music_dashboard.domain.listening.repositories import SyncRepositoryPort


class ListenPages(Protocol):
    def page(
        self,
        user: str,
        *,
        max_ts: int | None = None,
        min_ts: int | None = None,
        count: int = 1000,
    ) -> list[dict[str, Any]]: ...


class BoundaryAmbiguity(RuntimeError):
    """Timestamp-only API cannot prove completeness; authenticated export is required."""


class SyncBusy(RuntimeError):
    pass


def _timestamps(page: list[dict[str, Any]]) -> list[int]:
    stamps = [row.get("listened_at") for row in page]
    if any(type(stamp) is not int or stamp < 0 for stamp in stamps):
        raise ValueError("ListenBrainz page has an invalid timestamp; reconcile required")
    result = cast(list[int], stamps)  # Validated integer elements above.
    if result != sorted(result, reverse=True):
        raise ValueError("ListenBrainz page is not newest-first; reconcile required")
    return result


DEFAULT_RETRY_SECONDS = 900.0
MAX_RETRY_SECONDS = 86_400.0  # persisted deadlines never exceed 24 h past the failure


def failure_stats(error: Exception | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    # Honor transport retry hints across process restarts too. No outward adapter
    # dependency: a source error may expose a numeric retry_after in seconds.
    # Non-finite, negative, boolean or non-numeric hints are ignored; finite ones are
    # clamped to 24 h so a persisted deadline can never lock sync for long.
    delay = getattr(error, "retry_after", None)
    hint = (
        float(delay)
        if isinstance(delay, (int, float))
        and not isinstance(delay, bool)
        and math.isfinite(delay)
        and delay >= 0
        else None
    )
    floor = getattr(error, "min_retry_seconds", None)  # job-level only, e.g. oversized export
    job_floor = (
        float(floor)
        if isinstance(floor, (int, float)) and not isinstance(floor, bool) and math.isfinite(floor)
        else 0.0
    )
    seconds = min(max(DEFAULT_RETRY_SECONDS, hint or 0.0, job_floor), MAX_RETRY_SECONDS)
    retry = (now or datetime.now(UTC)) + timedelta(seconds=seconds)
    stats = {"next_retry_at": retry.isoformat()}
    if hint is not None:
        # The hint is upstream-wide, unlike an ordinary job-kind retry.
        stats["rate_limit_until"] = retry.isoformat()
    return stats


def sync_listens(
    client: ListenPages,
    repository: SyncRepositoryPort,
    user: str,
    *,
    now: datetime | None = None,
    overlap: timedelta = timedelta(hours=72),
) -> dict[str, Any]:
    """Initial history is export-only; incremental checks the bounded recent window.

    A cursor is persisted only AFTER its boundary second is cleared. Crash replay is
    idempotent. Last successful start anchors the overlap even after a lengthy outage.
    """
    if not user.strip():
        raise ValueError("Set LISTENBRAINZ_USERNAME before syncing")
    if overlap.total_seconds() <= 0:
        raise ValueError("Sync overlap must be positive")
    now = now or datetime.now(UTC)
    with repository.locked(user) as acquired:
        if not acquired:
            raise SyncBusy("Another sync owns the user lock; retry later")
        state = repository.load_checkpoint(user)
        if state.get("requires_reconcile"):
            raise BoundaryAmbiguity("Run music-dashboard reconcile to clear API ambiguity")
        scan = repository.load_scan(user)
        last_success = state.get("last_incremental_at")
        if scan and last_success and datetime.fromisoformat(scan["anchor_at"]) <= last_success:
            scan = None  # A successful export superseded an interrupted incremental.
        if scan is None:
            scan = {
                "anchor_at": now.isoformat(),
                "floor_ts": int(((last_success or now) - overlap).timestamp()),
                "upper_ts": int(now.timestamp()) + 1,
            }
        floor = int(scan["floor_ts"])
        cursor = state.get("cursor_max_ts")
        upper = int(cursor) if cursor is not None else int(scan["upper_ts"])
        run = repository.begin_run(user, "incremental", {"scan": scan})
        changed = 0
        pages = 0
        try:
            while True:
                page = client.page(user, max_ts=upper, count=1000)
                pages += 1
                if len(page) > 1000:
                    raise ValueError("ListenBrainz exceeded the page limit")
                try:
                    stamps = _timestamps(page)
                except ValueError:
                    # Retain malformed rows for diagnosis; never advance the cursor.
                    repository.ingest(user, page, "api", now)
                    raise
                if stamps and stamps[0] >= upper:
                    raise ValueError("ListenBrainz violated the exclusive timestamp bound")
                changed += repository.ingest(
                    user, [row for row in page if row["listened_at"] >= floor], "api", now
                )
                if len(page) < 1000 or stamps[-1] < floor:
                    break
                boundary = stamps[-1]
                tied = client.page(user, max_ts=boundary + 1, count=1000)
                pages += 1
                tied_stamps = _timestamps(tied)
                if len(tied) > 1000 or any(stamp > boundary for stamp in tied_stamps):
                    raise ValueError("ListenBrainz violated the boundary request")
                changed += repository.ingest(
                    user, [row for row in tied if row["listened_at"] >= floor], "api", now
                )
                if len(tied) == 1000 and tied_stamps[-1] == boundary:
                    repository.save_checkpoint(
                        user,
                        {
                            "requires_reconcile": True,
                            "ambiguous_boundary_ts": boundary,
                        },
                    )
                    raise BoundaryAmbiguity(
                        "At least 1000 listens share one second; run music-dashboard reconcile"
                    )
                # The API may change while paging. An empty/missing boundary cannot
                # prove that the rows at T were cleared: do not checkpoint through it.
                if not tied_stamps or boundary not in tied_stamps:
                    raise ValueError("Boundary changed while paging; retry or reconcile")
                upper = boundary
                repository.save_checkpoint(user, {"cursor_max_ts": upper})
            repository.save_checkpoint(
                user,
                {
                    "cursor_max_ts": None,
                    "last_incremental_at": datetime.fromisoformat(scan["anchor_at"]),
                    "ambiguous_boundary_ts": None,
                    "requires_reconcile": False,
                },
            )
            stats = {"changed": changed, "pages": pages}
            repository.finish_run(run, "succeeded", stats)
            return stats
        except Exception as exc:
            # Never persist arbitrary exception text: an upstream exception can carry
            # credentials, URLs or private payloads.
            status = "ambiguous" if isinstance(exc, BoundaryAmbiguity) else "failed"
            repository.finish_run(
                run, status, {**failure_stats(exc), "scan": scan}, type(exc).__name__
            )
            raise
