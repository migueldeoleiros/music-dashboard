from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ReleaseTitleCache(Base):
    """Positive projection of verified MusicBrainz titles; API reads all three columns."""

    __tablename__ = "release_title_cache"
    __table_args__ = (
        PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_release_title_cache"),
        CheckConstraint(
            "entity_type IN ('release','release_group')", name="ck_release_title_cache_type"
        ),
        CheckConstraint(
            "btrim(title) <> '' AND length(title) <= 1000", name="ck_release_title_cache_title"
        ),
    )
    entity_type: Mapped[str] = mapped_column(Text)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)


class ReleaseTitleLookup(Base):
    """Writer-only retry/negative state for title lookups (no API grant)."""

    __tablename__ = "release_title_lookup"
    __table_args__ = (
        PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_release_title_lookup"),
        CheckConstraint(
            "entity_type IN ('release','release_group')", name="ck_release_title_lookup_type"
        ),
        CheckConstraint(
            "status IN ('resolved','not_found','transient')", name="ck_release_title_lookup_status"
        ),
        CheckConstraint(
            "release_group_mbid IS NULL OR entity_type = 'release'",
            name="ck_release_title_lookup_rg_only_release",
        ),
        Index(
            "ix_release_title_lookup_next_retry_at",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )
    entity_type: Mapped[str] = mapped_column(Text)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    release_group_mbid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
