"""Stage an entire validated export before atomically activating any snapshot data.

Two distinct notions (see ``ExportSnapshot``):

* import-complete: verified account, rows exhausted without error, rows >= the lower bound.
  The run upserts, advances the checkpoint and succeeds, but never tombstones anything.
* deletion-safe: ONLY a trusted snapshot manifest (``snapshot.complete``). The real LB export
  status has no manifest, so real runs report ``deletions_blocked`` and delete nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from tempfile import TemporaryFile
from typing import Any, Protocol

from music_dashboard.application.sync_listens import SyncBusy, failure_stats
from music_dashboard.domain.listening.entities import ExportSnapshot
from music_dashboard.domain.listening.repositories import SyncRepositoryPort


class ListenExport(Protocol):
    def export(self, user: str) -> Iterable[dict[str, Any]]: ...


def reconcile_history(
    client: ListenExport,
    repository: SyncRepositoryPort,
    user: str,
    *,
    allow_large_delete: bool = False,
    now: datetime | None = None,
    bootstrap: bool = False,
) -> dict[str, int]:
    if not user.strip():
        raise ValueError("Set LISTENBRAINZ_USERNAME before importing history")
    snapshot_at = now or datetime.now(UTC)
    with repository.locked(user) as acquired:
        if not acquired:
            raise SyncBusy("Another sync owns the user lock; retry later")
        run = repository.begin_run(user, "export" if bootstrap else "reconcile")
        try:
            # Spool to disk rather than hydrate full history. Lazy download/parsing
            # failures happen BEFORE the repository can touch active history.
            with TemporaryFile(mode="w+t", encoding="utf-8") as staged:
                snapshot = client.export(user)
                if not isinstance(snapshot, ExportSnapshot) or (
                    snapshot.user.casefold() != user.casefold()
                ):
                    raise ValueError("Export is not bound to the configured account")
                # Deletion-safe needs a trusted manifest; import-complete does not.
                trusted = snapshot.complete and snapshot.expected_count is not None
                count = 0
                for row in snapshot:
                    if not isinstance(row, dict):
                        raise ValueError("Export contains a non-object record")
                    staged.write(json.dumps(row, allow_nan=False) + "\n")
                    count += 1
                if count == 0:
                    raise ValueError("Empty export cannot replace history; no data deleted")
                if snapshot.expected_count is not None and count != snapshot.expected_count:
                    raise ValueError("Export does not match the completed snapshot manifest")
                if snapshot.lower_bound is not None and count < snapshot.lower_bound:
                    raise ValueError("Export has fewer rows than the listen-count lower bound")
                staged.seek(0)

                def rows() -> Iterator[dict[str, Any]]:
                    for line in staged:
                        yield json.loads(line)

                stats = repository.activate_snapshot(
                    user,
                    rows(),
                    snapshot_at,
                    allow_large_delete=allow_large_delete,
                    snapshot_complete=trusted,
                )
            if trusted and stats.get("deletions_blocked"):
                raise ValueError("Export identity validation failed; reconcile is still required")
            stats = {
                **stats,
                "import_complete": 1,
                # Only a trusted manifest with every deletion check passed is deletion-safe.
                "deletion_safe": int(trusted and not stats.get("deletions_blocked")),
            }
            repository.save_checkpoint(
                user,
                {
                    "last_export_at": snapshot_at,
                    "last_incremental_at": snapshot_at,
                    "cursor_max_ts": None,
                    "ambiguous_boundary_ts": None,
                    "requires_reconcile": False,
                },
            )
            repository.finish_run(run, "succeeded", stats)
            return stats
        except Exception as exc:
            repository.finish_run(run, "failed", failure_stats(exc), type(exc).__name__)
            raise
