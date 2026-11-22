"""Worker-side access to the durable public-refresh singleton (writer role only).

All mutations are guarded by request id and expected status, so a stale caller can never
overwrite a newer request. Callers must hold the per-user sync advisory lock when they call
`claim`: only then is a persisted `running` row provably orphaned (serial lock, no lease).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

RUNNING_TTL = timedelta(minutes=30)
_DETAIL = re.compile(r"^[a-z_]{1,64}$")
_COLUMNS = "request_id, status, requested_at, expires_at"


@dataclass(frozen=True)
class RefreshRequest:
    request_id: uuid.UUID
    status: str
    requested_at: datetime | None
    expires_at: datetime


def _detail(value: str | None) -> str | None:
    if value is None:
        return None
    if not _DETAIL.fullmatch(value):
        raise ValueError("refresh detail must be a short lowercase code")
    return value


class RefreshRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def has_active(self) -> bool:
        """Cheap pre-check (no lock needed): is any request pending/running/deferred?"""
        with self._engine.connect() as c:
            return bool(
                c.execute(
                    text(
                        "SELECT 1 FROM refresh_state WHERE singleton "
                        "AND status IN ('pending','running','deferred')"
                    )
                ).first()
            )

    def claim(self) -> RefreshRequest | None:
        """Expire stale work, recover an orphaned `running` request, return the active one.

        Caller holds the sync lock, so no other worker can be running it: a `running` row
        means the previous worker died mid-run and the request goes back to `pending`
        (incremental sync is idempotent) unless it already expired.
        """
        with self._engine.begin() as c:
            row = (
                c.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM refresh_state "
                        "WHERE singleton AND status IN ('pending','running','deferred') "
                        "FOR UPDATE"
                    ),
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            # Evaluate after the row lock, not at the tick's scan anchor (or before
            # waiting for another transaction). Expiry follows the database clock.
            now = c.execute(text("SELECT clock_timestamp()")).scalar_one()
            if row["expires_at"] <= now:
                c.execute(
                    text(
                        "UPDATE refresh_state SET status='expired', finished_at=:now, "
                        "detail='expired' WHERE singleton"
                    ),
                    {"now": now},
                )
                return None
            if row["status"] == "running":
                c.execute(
                    text(
                        "UPDATE refresh_state SET status='pending', started_at=NULL, "
                        "detail='worker_restarted', "
                        "expires_at = LEAST(expires_at, :now + make_interval(secs => 900)) "
                        "WHERE singleton"
                    ),
                    {"now": now},
                )
            return RefreshRequest(
                row["request_id"], row["status"], row["requested_at"], row["expires_at"]
            )

    def _transition(
        self, request_id: uuid.UUID, sets: str, allowed: tuple[str, ...], params: dict[str, Any]
    ) -> bool:
        statuses = ", ".join(f"'{s}'" for s in allowed)
        with self._engine.begin() as c:
            # UPDATE predicates can be evaluated before waiting on a row lock. Lock
            # first, then evaluate the database clock in subsequent statements, even
            # when an admission transaction locked this row without changing it.
            c.execute(text("SELECT singleton FROM refresh_state WHERE singleton FOR UPDATE"))
            # Read-side expiry is terminal too: a slow worker must not turn an already
            # expired UUID into success after the UI stopped waiting for it.
            c.execute(
                text(
                    "UPDATE refresh_state SET status='expired', finished_at=expires_at, "
                    "detail='expired' WHERE singleton AND request_id=:rid "
                    "AND status IN ('pending','running','deferred') "
                    "AND expires_at <= clock_timestamp()"
                ),
                {"rid": request_id},
            )
            return (
                c.execute(
                    text(
                        f"UPDATE refresh_state SET {sets} WHERE singleton "
                        f"AND request_id = :rid AND status IN ({statuses}) "
                        "AND expires_at > clock_timestamp()"
                    ),
                    {"rid": request_id, **params},
                ).rowcount
                == 1
            )

    def mark_running(self, request_id: uuid.UUID) -> bool:
        return self._transition(
            request_id,
            "status='running', started_at=clock_timestamp(), retry_at=NULL, detail=NULL, "
            "expires_at = LEAST(expires_at, clock_timestamp() + make_interval(secs => :ttl))",
            ("pending", "deferred"),
            {"ttl": RUNNING_TTL.total_seconds()},
        )

    def mark_deferred(
        self, request_id: uuid.UUID, detail: str, retry_at: datetime | None = None
    ) -> bool:
        return self._transition(
            request_id,
            "status='deferred', detail=:d, retry_at=:r",
            ("pending", "deferred", "running"),
            {"d": _detail(detail), "r": retry_at},
        )

    def finish(
        self,
        request_id: uuid.UUID,
        status: str,
        detail: str | None = None,
        retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> bool:
        if status not in {"succeeded", "failed"}:
            raise ValueError("invalid terminal refresh status")
        return self._transition(
            request_id,
            "status=:s, finished_at=:now, detail=:d, retry_at=:r",
            ("pending", "deferred", "running"),
            {"s": status, "now": now or datetime.now(UTC), "d": _detail(detail), "r": retry_at},
        )
