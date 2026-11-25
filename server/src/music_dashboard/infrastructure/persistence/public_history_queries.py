"""Bounded chronological queries; private sort keys never leave the application boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unicodedata import normalize

from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from music_dashboard.infrastructure.persistence.artwork_resolver import (
    resolve_song_artwork,
    song_keys,
)
from music_dashboard.infrastructure.persistence.statistics_queries import CASEFOLD, WHITESPACE
from music_dashboard.presentation.api.schemas.history import ArtistCredit, HistoryItem

GENERATION_SQL = """
SELECT ingestion_generation, enrichment_generation, CURRENT_TIMESTAMP AS reference
FROM data_generation WHERE id = 1
"""

# LIMIT is applied to listens BEFORE loading credits. Repeated plays remain separate rows.
PAGE_SQL = """
WITH page AS MATERIALIZED (
 SELECT id, listened_at, track_name, artist_name, release_name,
        recording_mbid, release_group_mbid, release_mbid
 FROM listens
 WHERE listened_at <= CAST(:reference AS timestamptz)
 {boundary}
 ORDER BY listened_at DESC, id DESC LIMIT :fetch_limit
), calendar AS (
 SELECT *, (listened_at AT TIME ZONE :tz)::date AS day,
        (CAST(:reference AS timestamptz) AT TIME ZONE :tz)::date AS today,
        date_trunc('week', CAST(:reference AS timestamptz) AT TIME ZONE :tz)::date AS monday
 FROM page
)
SELECT id, listened_at, track_name, artist_name, release_name,
 recording_mbid, release_group_mbid, release_mbid,
 CASE WHEN day = today THEN 'Today'
      WHEN day = today - 1 THEN 'Yesterday'
      WHEN day >= monday THEN 'This Week'
      WHEN day >= monday - 7 THEN 'Last Week'
      ELSE to_char(day, 'FMMonth YYYY') END AS heading
FROM calendar ORDER BY listened_at DESC, id DESC
"""
BOUNDARY_SQL = "AND (listened_at, id) < (CAST(:last_at AS timestamptz), :last_id)"
CREDITS_SQL = """
SELECT listen_id, artist_name, artist_mbid FROM listen_artists
WHERE listen_id = ANY(:ids) ORDER BY listen_id, position LIMIT 10001
"""


@dataclass(frozen=True)
class HistoryBatch:
    items: list[HistoryItem]
    generation: str
    reference: datetime
    boundary: tuple[datetime, int] | None


def credit(name: str, mbid: Any) -> ArtistCredit:
    label = name.strip()
    key = str(mbid) if mbid else "name:" + normalize("NFKC", name).strip().casefold()
    return ArtistCredit(key=key if label else None, label=label or "[Missing artist]")


class SQLPublicHistory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def fetch(
        self,
        tz: str,
        limit: int,
        reference: datetime | None = None,
        generation: str | None = None,
        boundary: tuple[datetime, int] | None = None,
    ) -> HistoryBatch:
        try:
            with self._engine.connect() as conn, conn.begin():
                # Generation, page and credits must describe the same listening snapshot.
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                meta = conn.execute(text(GENERATION_SQL)).mappings().one()
                current_generation = (
                    f"{meta['ingestion_generation']}:{meta['enrichment_generation']}"
                )
                if generation is not None and generation != current_generation:
                    raise HTTPException(409, "Listening data changed; reload feed")
                reference = reference or meta["reference"]
                params: dict[str, Any] = {
                    "reference": reference,
                    "tz": tz,
                    "fetch_limit": limit + 1,
                }
                if boundary is not None:
                    params.update(last_at=boundary[0], last_id=boundary[1])
                query = PAGE_SQL.format(boundary=BOUNDARY_SQL if boundary is not None else "")
                rows = conn.execute(text(query), params).mappings().all()
                visible = rows[:limit]
                credits: dict[int, list[ArtistCredit]] = {}
                if visible:
                    artists = (
                        conn.execute(text(CREDITS_SQL), {"ids": [row["id"] for row in visible]})
                        .mappings()
                        .all()
                    )
                    if len(artists) > 10000:
                        raise HTTPException(503, "History temporarily unavailable")
                    for artist in artists:
                        item = credit(artist["artist_name"], artist["artist_mbid"])
                        bucket = credits.setdefault(artist["listen_id"], [])
                        if item not in bucket:
                            bucket.append(item)
                covers = resolve_song_artwork(
                    conn,
                    [
                        song_keys(
                            recording_mbid=row["recording_mbid"],
                            release_group_mbid=row["release_group_mbid"],
                            release_mbid=row["release_mbid"],
                            artist_name=row["artist_name"],
                            track_name=row["track_name"],
                            release_name=row["release_name"],
                            primary_artist_key=(
                                credits[row["id"]][0].key if credits.get(row["id"]) else None
                            ),
                            whitespace=WHITESPACE,
                        )
                        for row in visible
                    ],
                    {"casefold": CASEFOLD, "whitespace": WHITESPACE},
                )
                items = [
                    HistoryItem(
                        image_url=url,
                        title=(row["track_name"] or "").strip() or "[Missing title]",
                        album=(row["release_name"] or "").strip() or None,
                        artists=credits.get(row["id"])
                        or [
                            ArtistCredit(
                                key=None,
                                label=(row["artist_name"] or "").strip() or "[Missing artist]",
                            )
                        ],
                        group=row["heading"],
                    )
                    for row, url in zip(visible, covers, strict=True)
                ]
                next_boundary = (
                    (visible[-1]["listened_at"], visible[-1]["id"]) if len(rows) > limit else None
                )
                return HistoryBatch(items, current_generation, reference, next_boundary)
        except SQLAlchemyError:
            raise HTTPException(503, "History temporarily unavailable") from None
