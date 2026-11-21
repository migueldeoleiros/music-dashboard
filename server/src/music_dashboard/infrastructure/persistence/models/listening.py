"""Normalized listens. API-readable; contains no raw payload."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Listen(Base):
    __tablename__ = "listens"
    __table_args__ = (
        UniqueConstraint("user_name", "listened_at", "recording_msid"),
        Index("ix_listens_listened_at", "listened_at"),
        Index(
            "ix_listens_recording_mbid",
            "recording_mbid",
            postgresql_where=text("recording_mbid IS NOT NULL"),
        ),
        Index(
            "ix_listens_release_group_mbid",
            "release_group_mbid",
            postgresql_where=text("release_group_mbid IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_name: Mapped[str] = mapped_column(Text)
    listened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recording_msid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    track_name: Mapped[str | None] = mapped_column(Text)
    artist_name: Mapped[str | None] = mapped_column(Text)  # credit string as submitted
    release_name: Mapped[str | None] = mapped_column(Text)
    recording_mbid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    release_mbid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    release_group_mbid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListenArtist(Base):
    """Credited artists per listen (multi-credit). mbid NULL when unmapped."""

    __tablename__ = "listen_artists"
    __table_args__ = (
        Index(
            "ix_listen_artists_artist_mbid",
            "artist_mbid",
            postgresql_where=text("artist_mbid IS NOT NULL"),
        ),
    )
    listen_id: Mapped[int] = mapped_column(
        ForeignKey("listens.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    artist_name: Mapped[str] = mapped_column(Text)
    artist_mbid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
