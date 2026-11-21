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


class Artwork(Base):
    """Resolved image URL per exact MBID. URLs only; API role reads image_url/status columns."""

    __tablename__ = "artwork"
    __table_args__ = (
        PrimaryKeyConstraint("entity_type", "entity_mbid", name="pk_artwork"),
        CheckConstraint("entity_type IN ('album','track','artist')", name="ck_artwork_entity_type"),
        CheckConstraint("source IN ('deezer','caa','none')", name="ck_artwork_source"),
        CheckConstraint("status IN ('resolved','miss','transient')", name="ck_artwork_status"),
        CheckConstraint(
            "image_url IS NULL OR image_url ~ '^https://[^[:space:]]+$'",
            name="ck_artwork_image_url",
        ),
        CheckConstraint(
            "(status = 'resolved') = (image_url IS NOT NULL AND source <> 'none')",
            name="ck_artwork_resolved_has_url",
        ),
        Index(
            "ix_artwork_next_retry_at",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )
    entity_type: Mapped[str] = mapped_column(Text)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
