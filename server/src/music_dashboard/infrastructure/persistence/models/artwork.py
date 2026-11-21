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

NAME_KINDS = "'artist_name','artist_alias','album_title_artist','album_title','track_title_artist'"


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
            "(status = 'resolved') = (image_url IS NOT NULL) "
            "AND (status = 'resolved') = (source <> 'none')",
            name="ck_artwork_resolved_has_url",
        ),
        CheckConstraint(
            "match_kind IN ('id'," + NAME_KINDS + ") AND (match_kind = 'id' OR "
            "(status = 'resolved' AND source = 'deezer' AND provider_ref IS NOT NULL))",
            name="ck_artwork_match_kind",
        ),
        CheckConstraint(
            "provider_ref IS NULL OR provider_ref ~ '^[1-9][0-9]{0,15}$'",
            name="ck_artwork_provider_ref",
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
    # Writer-only provenance: 'id' = exact-ID chain, else a name-match kind + provider id.
    match_kind: Mapped[str] = mapped_column(Text, server_default="id")
    provider_ref: Mapped[str | None] = mapped_column(Text)


class ArtworkName(Base):
    """Image URL per stable ranking key (`name:...`) for entities without any MBID."""

    __tablename__ = "artwork_name"
    __table_args__ = (
        PrimaryKeyConstraint("entity_type", "name_key", name="pk_artwork_name"),
        CheckConstraint(
            "entity_type IN ('album','track','artist')", name="ck_artwork_name_entity_type"
        ),
        CheckConstraint(
            "name_key LIKE 'name:%' AND length(name_key) <= 1000", name="ck_artwork_name_key"
        ),
        CheckConstraint("source IN ('deezer','none')", name="ck_artwork_name_source"),
        CheckConstraint("status IN ('resolved','miss','transient')", name="ck_artwork_name_status"),
        CheckConstraint(
            "image_url IS NULL OR image_url ~ '^https://[^[:space:]]+$'",
            name="ck_artwork_name_image_url",
        ),
        CheckConstraint(
            "(status = 'resolved') = (image_url IS NOT NULL) "
            "AND (status = 'resolved') = (source <> 'none')",
            name="ck_artwork_name_resolved_has_url",
        ),
        CheckConstraint(
            "((status = 'resolved') = (match_kind IN (" + NAME_KINDS + "))) "
            "AND ((status = 'resolved') = (provider_ref IS NOT NULL)) "
            "AND (provider_ref IS NULL OR provider_ref ~ '^[1-9][0-9]{0,15}$')",
            name="ck_artwork_name_provenance",
        ),
        Index(
            "ix_artwork_name_next_retry_at",
            "next_retry_at",
            postgresql_where=text("next_retry_at IS NOT NULL"),
        ),
    )
    entity_type: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    match_kind: Mapped[str] = mapped_column(Text, server_default="none")
    provider_ref: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
