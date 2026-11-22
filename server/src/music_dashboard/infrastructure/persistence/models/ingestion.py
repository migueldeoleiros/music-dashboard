"""Raw/provenance and sync bookkeeping. NEVER readable by the API role."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RawListen(Base):
    """Verbatim LB listen. Identity (user, listened_at, msid) is stable across remaps."""

    __tablename__ = "raw_listens"
    __table_args__ = (
        UniqueConstraint("user_name", "listened_at", "recording_msid"),
        Index(
            "ix_raw_listens_active_listened_at",
            "user_name",
            "listened_at",
            postgresql_where=text("tombstoned_at IS NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_name: Mapped[str] = mapped_column(Text)
    listened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recording_msid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # e.g. {"source": "export"|"api", "snapshot_id": ..., "fetched_at": ...}
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Tombstone: upstream deletion observed; row kept for audit/rebuild safety.
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tombstone_reason: Mapped[str | None] = mapped_column(Text)


class QuarantinedListen(Base):
    """Rows missing required identity; preserved, never merged or guessed."""

    __tablename__ = "quarantined_listens"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_name: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    missing_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("kind IN ('incremental','export','reconcile')", name="kind"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','ambiguous','aborted')", name="status"
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    user_name: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    __tablename__ = "sync_state"
    user_name: Mapped[str] = mapped_column(Text, primary_key=True)
    cursor_max_ts: Mapped[int | None] = mapped_column(BigInteger)
    last_incremental_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_export_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ambiguous_boundary_ts: Mapped[int | None] = mapped_column(BigInteger)
    requires_reconcile: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshState(Base):
    """One-row durable public-refresh request. API role: no grants (definer functions only)."""

    __tablename__ = "refresh_state"
    __table_args__ = (
        CheckConstraint("singleton", name="singleton"),
        CheckConstraint(
            "status IN ('idle','pending','running','succeeded','failed','expired','deferred')",
            name="status",
        ),
        CheckConstraint("detail ~ '^[a-z_]{1,64}$'", name="detail"),
        CheckConstraint(
            "status NOT IN ('pending','running','deferred') OR "
            "(request_id IS NOT NULL AND expires_at IS NOT NULL)",
            name="inflight",
        ),
    )
    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'idle'"))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(Text)
