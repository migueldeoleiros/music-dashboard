"""Idempotent synthetic seed. `seed_fixtures(engine)`; needs write privileges (owner/writer)."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, func, update
from sqlalchemy.dialects.postgresql import insert

from . import seed_data as d
from .models import (
    ArtistCountry,
    DataGeneration,
    EnrichmentQueueItem,
    EntityGenre,
    Genre,
    Listen,
    ListenArtist,
    MetadataCache,
    RawListen,
)


def _mbid(kind: str, key: str | None) -> uuid.UUID | None:
    return d.uid(f"{kind}:{key}") if key else None


def seed_fixtures(engine: Engine) -> dict[str, int]:
    """Load representative synthetic data. Safe to re-run. Returns row counts."""
    with engine.begin() as c:
        for k, name in d.GENRES.items():
            c.execute(
                insert(Genre)
                .values(genre_mbid=d.uid(f"genre:{k}"), name=name)
                .on_conflict_do_nothing()
            )
        for k, (_, country) in d.ARTISTS.items():
            if country:
                c.execute(
                    insert(ArtistCountry)
                    .values(artist_mbid=d.uid(f"artist:{k}"), country_code=country)
                    .on_conflict_do_nothing()
                )
        for etype, key, genres in d.ENTITY_GENRES:
            for g, votes in genres.items():
                c.execute(
                    insert(EntityGenre)
                    .values(
                        entity_type=etype,
                        entity_mbid=d.uid(f"{etype}:{key}"),
                        genre_mbid=d.uid(f"genre:{g}"),
                        votes=votes,
                    )
                    .on_conflict_do_nothing()
                )
        for i, row in enumerate(d.LISTENS):
            at = d.ts(row["at"])
            msid = d.uid(f"msid:{i}:{row['key']}")
            rec, rg = row["rec"], row["rg"]
            vals = dict(
                user_name=d.FIXTURE_USER,
                listened_at=at,
                recording_msid=msid,
                track_name=row["track"],
                artist_name=" & ".join(n for _, n in row["artists"]),
                release_name=row["release"],
                recording_mbid=_mbid("recording", rec),
                release_mbid=_mbid("release", rg),
                release_group_mbid=_mbid("release_group", rg),
            )
            lid = c.execute(
                insert(Listen)
                .values(**vals)
                .on_conflict_do_update(
                    constraint="uq_listens_user_name_listened_at_recording_msid",
                    set_={"track_name": vals["track_name"]},
                )
                .returning(Listen.id)
            ).scalar_one()
            for pos, (akey, aname) in enumerate(row["artists"]):
                c.execute(
                    insert(ListenArtist)
                    .values(
                        listen_id=lid,
                        position=pos,
                        artist_name=aname,
                        artist_mbid=d.uid(f"artist:{akey}") if akey else None,
                    )
                    .on_conflict_do_nothing()
                )
            c.execute(
                insert(RawListen)
                .values(
                    user_name=d.FIXTURE_USER,
                    listened_at=at,
                    recording_msid=msid,
                    payload={
                        "listened_at": int(at.timestamp()),
                        "recording_msid": str(msid),
                        "track_metadata": {
                            "track_name": row["track"],
                            "artist_name": vals["artist_name"],
                            "release_name": row["release"],
                            "mbid_mapping": (
                                {"recording_mbid": str(vals["recording_mbid"])} if rec else None
                            ),
                        },
                    },
                    provenance={"source": "synthetic-fixture"},
                )
                .on_conflict_do_nothing()
            )
        # representative cache/queue states: positive, negative (empty), transient error, pending
        for status, key in (
            ("positive", "r_rec"),
            ("empty", "r_single"),
            ("transient_error", "r_xw"),
        ):
            c.execute(
                insert(MetadataCache)
                .values(
                    entity_type="recording",
                    entity_mbid=d.uid(f"recording:{key}"),
                    source="listenbrainz",
                    status=status,
                    payload={"synthetic": True} if status == "positive" else None,
                    last_error="HTTP 503" if status == "transient_error" else None,
                )
                .on_conflict_do_nothing()
            )
        c.execute(
            insert(EnrichmentQueueItem)
            .values(entity_type="artist", entity_mbid=d.uid("artist:nocountry"), priority=1)
            .on_conflict_do_nothing()
        )
        c.execute(
            update(DataGeneration)
            .where(DataGeneration.id == 1)
            .values(
                ingestion_generation=func.greatest(DataGeneration.ingestion_generation, 1),
                enrichment_generation=func.greatest(DataGeneration.enrichment_generation, 1),
                last_updated=func.now(),
            )
        )
        n = c.execute(func.count().select().select_from(Listen)).scalar_one()
    return {"listens": n}
