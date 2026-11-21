from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

ENTITY_TYPES = "('recording','release_group','artist')"


class MetadataCache(Base):
    """Enrichment cache incl. negative cache. status == domain CacheStatus. Not API-readable."""

    __tablename__ = "metadata_cache"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_mbid", "source"),
        CheckConstraint(f"entity_type IN {ENTITY_TYPES}", name="entity_type"),
        CheckConstraint("source IN ('listenbrainz','musicbrainz')", name="source"),
        CheckConstraint(
            "status IN ('pending','positive','empty','transient_error')", name="status"
        ),
        Index(
            "ix_metadata_cache_next_retry_at",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)


class EnrichmentQueueItem(Base):
    __tablename__ = "enrichment_queue"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_mbid"),
        CheckConstraint(f"entity_type IN {ENTITY_TYPES}", name="entity_type"),
        CheckConstraint("status IN ('pending','in_progress','done','failed')", name="status"),
        Index("ix_enrichment_queue_pick", "status", "next_retry_at", "priority"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    priority: Mapped[int] = mapped_column(Integer, server_default=text("0"))  # e.g. listen count
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Genre(Base):
    """Recognized genres only (have genre_mbid); free tags are never stored here."""

    __tablename__ = "genres"
    genre_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class EntityGenre(Base):
    """Resolved genre votes per entity level. votes may be <= 0; no check."""

    __tablename__ = "entity_genres"
    __table_args__ = (CheckConstraint(f"entity_type IN {ENTITY_TYPES}", name="entity_type"),)
    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    genre_mbid: Mapped[uuid.UUID] = mapped_column(ForeignKey("genres.genre_mbid"), primary_key=True)
    votes: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtistCountry(Base):
    """Credited artist ISO country. XW/XE stored as-is; omitted by statistics."""

    __tablename__ = "artist_countries"
    __table_args__ = (CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code"),)
    artist_mbid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    country_code: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataGeneration(Base):
    """Single row; bumps invalidate statistics caches."""

    __tablename__ = "data_generation"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default=text("1"))
    ingestion_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    enrichment_generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
