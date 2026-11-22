"""Public artist reads, strict DTO projection, and bounded opaque pagination."""

from __future__ import annotations

import secrets
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from music_dashboard.config import settings
from music_dashboard.infrastructure.persistence import artist_queries as queries
from music_dashboard.infrastructure.persistence.database import create_db_engine
from music_dashboard.infrastructure.persistence.timezones import load_timezones
from music_dashboard.presentation.api.schemas.history import (
    ArtistHeatmap,
    ArtistRankingPage,
    ArtistRecentPage,
    ArtistSummary,
    PublicSong,
)


class ArtistCursorStore:
    """Process-local, bounded, random tokens; restart/expiry means reload (409)."""

    def __init__(self) -> None:
        self.entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.lock = Lock()

    def issue(self, state: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.entries[token] = (monotonic() + 900, state)
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
        return token

    def read(self, token: str) -> dict[str, Any]:
        if len(token) > 128:
            raise HTTPException(409, "Pagination expired; reload")
        with self.lock:
            entry = self.entries.get(token)
            if entry is None or entry[0] <= monotonic():
                self.entries.pop(token, None)
                raise HTTPException(409, "Pagination expired; reload")
            return entry[1].copy()


cursor_store = ArtistCursorStore()


def _key(value: str) -> str:
    if value.startswith("name:"):
        name = value[5:]
        if (
            not name
            or len(name) > 1000
            or unicodedata.normalize("NFKC", name).strip().casefold() != name
        ):
            raise HTTPException(400, "Invalid artist key")
        return value
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid artist key") from None


def _limit(value: int) -> None:
    if not 1 <= value <= 100:
        raise HTTPException(400, "Invalid page limit")


class ArtistHistory:
    def __init__(self, engine: Engine | None = None) -> None:
        # Fixture mode has no artist-history fixture; fail safely, not against default DB.
        self.engine = engine

    @contextmanager
    def _snapshot(self) -> Iterator[tuple[Connection, str, datetime]]:
        if self.engine is None and settings.fixture_mode:
            raise HTTPException(503, "Artist data unavailable")
        try:
            if self.engine is None:
                self.engine = create_db_engine(settings.database_url)
            with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
                c.execute(text("SET TRANSACTION READ ONLY"))
                c.execute(text("SELECT set_config('statement_timeout','5000',true)"))
                # JIT adds ~1s compile to these large plans; row counts are small.
                c.execute(text("SELECT set_config('jit','off',true)"))
                row = c.execute(
                    text("""SELECT ingestion_generation, enrichment_generation,
                    now() AS reference FROM data_generation WHERE id=1""")
                ).one()
                yield c, f"{row.ingestion_generation}:{row.enrichment_generation}", row.reference
        except SQLAlchemyError:
            raise HTTPException(503, "Artist data unavailable") from None

    @staticmethod
    def _identity(c: Connection, p: dict[str, Any]) -> dict[str, Any]:
        found = queries.identity(c, p)
        if found is None:
            raise HTTPException(404, "Artist not found")
        return found

    @staticmethod
    def _page_state(
        cursor: str | None,
        query: tuple[Any, ...],
        generation: str,
        reference: datetime,
    ) -> dict[str, Any]:
        if cursor is None:
            return {"query": query, "generation": generation, "reference": reference, "after": None}
        state = cursor_store.read(cursor)
        if state["query"] != query or state["generation"] != generation:
            raise HTTPException(409, "Pagination changed; reload")
        return state

    def summary(self, artist_key: str) -> ArtistSummary:
        key = _key(artist_key)
        p = queries.params(key)
        with self._snapshot() as (c, _, _reference):
            found = self._identity(c, p)
            genres = (
                []
                if p["mbid"] is None
                else list(
                    c.execute(
                        text("""
                SELECT g.name FROM entity_genres e JOIN genres g USING(genre_mbid)
                WHERE e.entity_type='artist' AND e.entity_mbid=CAST(:mbid AS uuid)
                ORDER BY e.votes DESC,g.name,g.genre_mbid LIMIT 50
            """),
                        p,
                    ).scalars()
                )
            )
            return ArtistSummary(
                key=key,
                label=found["label"],
                total_listens=found["total"],
                genres=genres,
                image_url=queries.artwork(c, key, "artist"),
                byline="From your listening history · all-time plays",
            )

    def rankings(
        self,
        artist_key: str,
        dimension: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ArtistRankingPage:
        key = _key(artist_key)
        _limit(limit)
        if dimension not in ("albums", "tracks"):
            raise HTTPException(400, "Invalid ranking dimension")
        p = queries.params(key)
        with self._snapshot() as (c, generation, reference):
            state = self._page_state(
                cursor, ("artist-ranking", key, dimension, limit), generation, reference
            )
            self._identity(c, p)
            rows = queries.rankings(c, p, dimension, limit, state["after"])
            more = len(rows) > limit
            rows = rows[:limit]
            for row in rows:
                row["image_url"] = queries.artwork(
                    c, row["key"], "album" if dimension == "albums" else "track"
                )
            next_cursor = None
            if more:
                state["after"] = (rows[-1]["listens"], rows[-1]["key"])
                next_cursor = cursor_store.issue(state)
            return ArtistRankingPage.model_validate(
                {
                    "dimension": dimension,
                    "items": rows,
                    "generation": generation,
                    "next_cursor": next_cursor,
                }
            )

    def heatmap(self, artist_key: str, year: int | None = None, tz: str = "UTC") -> ArtistHeatmap:
        key = _key(artist_key)
        zones = load_timezones()
        if tz not in zones:
            raise HTTPException(400, "Unsupported IANA timezone")
        tz = zones[tz]
        with self._snapshot() as (c, _, reference):
            if year is None:
                year = reference.astimezone(ZoneInfo(tz)).year
            if not 1 <= year <= 9999:
                raise HTTPException(400, "Invalid year")
            p = queries.params(key, tz)
            self._identity(c, p)
            years = list(
                c.execute(
                    text(
                        queries.MATCH
                        + """
                SELECT DISTINCT extract(year FROM day)::int AS year FROM selected ORDER BY year
            """
                    ),
                    p,
                ).scalars()
            )
            rows = c.execute(
                text(
                    queries.MATCH
                    + """
                SELECT day,count(*) AS listens FROM selected
                WHERE extract(year FROM day)=:year GROUP BY day ORDER BY day
            """
                ),
                {**p, "year": year},
            ).mappings()
            return ArtistHeatmap.model_validate(
                {
                    "year": year,
                    "years": years,
                    "timezone": tz,
                    "cells": [dict(row) for row in rows],
                }
            )

    def recent(
        self,
        artist_key: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ArtistRecentPage:
        if settings.fixture_mode or not getattr(settings, "public_event_feeds_enabled", False):
            raise HTTPException(503, "Public event feeds unavailable")
        key = _key(artist_key)
        _limit(limit)
        p = queries.params(key)
        with self._snapshot() as (c, generation, reference):
            state = self._page_state(cursor, ("artist-recent", key, limit), generation, reference)
            self._identity(c, p)
            rows = queries.recent(c, p, limit, state["after"])
            more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = None
            if more:
                state["after"] = (rows[-1]["listened_at"].isoformat(), rows[-1]["id"])
                next_cursor = cursor_store.issue(state)
            # Explicit allowlist: private ordering values never cross the DTO boundary.
            return ArtistRecentPage(
                items=[
                    PublicSong(title=r["title"], album=r["album"], artists=r["artists"])
                    for r in rows
                ],
                generation=generation,
                next_cursor=next_cursor,
            )

    def credits(self, dimension: str, key: str) -> dict[str, Any]:
        """Reliable credits for a ranked album/track key; empty unless all listens agree.

        Only MBID keys resolve (name: keys embed no individually credited identity)."""
        if dimension not in ("albums", "tracks"):
            raise HTTPException(400, "Invalid dimension")
        try:
            mbid = str(UUID(key))
        except (ValueError, AttributeError):
            if key.startswith("name:") and len(key) <= 2000:
                return {"artists": []}
            raise HTTPException(400, "Invalid key") from None
        with self._snapshot() as (c, _, _reference):
            sets = queries.credit_sets(c, dimension, mbid)
        if len(sets) != 1:
            return {"artists": []}
        return {"artists": sets[0]}
