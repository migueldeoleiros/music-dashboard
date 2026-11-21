"""Deterministic larger synthetic seed for integration/Compose testing (no network, no tokens).

    cd server && MUSIC_DATABASE_URL=postgresql://writer@host/db \
        uv run python scripts/seed_integration.py --confirm-synthetic
    uv run python scripts/seed_integration.py --plan      # no DB; prints partitions + digest
    ... --verify                                           # prints a DB content digest
    ... --skip-enrichment                                  # raw/normalized listens only

Data (seed fixed; same output every run, independent of wall clock):
* user ``synthetic_integration``; 8000 valid listens 2018-01-01..2026-10-31 UTC, 6 quarantined rows
* 40 artists, 20 genres, 20 countries (+ XW pseudo-country artist, + artist without country)
* collaborations (multi-credit), unmapped listens, partially mapped listens, missing
  track/release names, same-second listens
* three date partitions with era-scoped catalogs, so enrichment can be 0% / partial / 100%:
  zero (2018-2019), partial (2020-2022), full (2023-01-01..2026-10-31)

Listens go through the REAL ingestion path (SyncRepository.ingest = raw_listens + normalizer),
metadata through the REAL cache format (normalize_entry payloads, SqlMetadataRepository
.save_lookup/.complete). Re-running changes nothing; truncating listens and re-ingesting
raw_listens payloads, or re-running this script, reproduces the same digest.

Default cache is replayable (positive/empty only); transient_error rows need --include-transient.
Precondition: no concurrent writers (worker/sync/API writer) while seeding; the safety preflight
is a check-then-write, not a lock. Stop the worker first.

Safety: needs MUSIC_DATABASE_URL (no default, so the read-only API role is never guessed), the
``--confirm-synthetic`` flag, and refuses a DB holding any data outside this synthetic catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import music_dashboard  # noqa: F401
except ImportError:  # plain `python scripts/seed_integration.py` without installed package
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from music_dashboard.application.enrich_metadata import EnrichmentPolicy, recognized_votes
from music_dashboard.domain.metadata.entities import CachedLookup, EnrichmentWork
from music_dashboard.domain.metadata.policies import valid_country
from music_dashboard.infrastructure.listenbrainz.metadata import normalize_entry

SEED = 20181101
USER = "synthetic_integration"
_NS = uuid.UUID("5e0d1a7c-1b2c-4d3e-8f40-0000005eed01")
INGESTED_AT = datetime(2026, 11, 1, tzinfo=UTC)  # fixed "fetched/enriched" timestamp
POLICY = EnrichmentPolicy()
TOTAL_LISTENS = 8000
QUARANTINE_ROWS = 6
BATCH = 1000


def uid(key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, key)


@dataclass(frozen=True)
class Era:
    name: str
    start: datetime  # inclusive
    end: datetime  # exclusive
    listens: int
    artists: int
    enrichment: str  # zero | partial | full

    @property
    def last_second(self) -> datetime:
        return self.end - timedelta(seconds=1)


ERAS = (
    Era(
        "zero", datetime(2018, 1, 1, tzinfo=UTC), datetime(2020, 1, 1, tzinfo=UTC), 1500, 10, "zero"
    ),
    Era(
        "partial",
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2023, 1, 1, tzinfo=UTC),
        2500,
        12,
        "partial",
    ),
    Era(
        "full",
        datetime(2023, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
        4000,
        18,
        "full",
    ),
)

GENRES = [
    "rock", "pop", "electronic", "jazz", "folk", "hip hop", "metal", "punk", "ambient", "blues",
    "classical", "country", "reggae", "soul", "funk", "techno", "house", "indie", "latin", "disco",
]  # fmt: skip
COUNTRIES = [
    "JP", "US", "RO", "GB", "DE", "FR", "SE", "BR", "NG", "ZA",
    "IN", "KR", "MX", "AU", "CA", "IT", "ES", "NL", "PL", "AR",
]  # fmt: skip
_ADJ = ["Blue", "Silent", "Golden", "Neon", "Paper", "Velvet", "Crimson", "Lunar"]
_NOUN = [
    "Harbor",
    "Echo",
    "Orchard",
    "Signal",
    "Garden",
    "Engine",
    "Tide",
    "Canyon",
    "Lantern",
    "Pilot",
]
UNMAPPED_ARTISTS = ["Unknown Band", "Basement Tapes", "Street Busker", "Anonymous Choir"]


# ---------------------------------------------------------------- catalog


@dataclass
class Artist:
    key: str
    name: str
    era: str
    mbid: uuid.UUID
    country: str | None
    genres: list[tuple[int, int]]  # (genre index, votes)


@dataclass
class Track:
    key: str
    title: str
    era: str
    recording: uuid.UUID
    release: uuid.UUID
    release_group: uuid.UUID
    release_name: str
    artists: list[Artist]  # credited, in order; first is primary
    genres: list[tuple[int, int]]  # recording-level votes (may be empty)
    rg_genres: list[tuple[int, int]]


@dataclass
class Dataset:
    artists: list[Artist]
    tracks: list[Track]
    payloads: list[dict[str, Any]]  # valid LB export rows, chronological
    invalid: list[dict[str, Any]]  # rows that must be quarantined
    era_of: dict[tuple[str, uuid.UUID], str] = field(default_factory=dict)


def genre_mbid(i: int) -> uuid.UUID:
    return uid(f"genre:{GENRES[i]}")


def _votes(rng: random.Random, lo: int = 1, hi: int = 3) -> list[tuple[int, int]]:
    picks = rng.sample(range(len(GENRES)), rng.randint(lo, hi))
    return [(g, rng.choice([1, 2, 3, 5, 8, 13])) for g in picks]


def build_catalog(rng: random.Random) -> tuple[list[Artist], list[Track]]:
    names = [f"{a} {n}" for a in _ADJ for n in _NOUN][:40]
    rng.shuffle(names)
    artists: list[Artist] = []
    tracks: list[Track] = []
    idx = 0
    country_offset = 0
    for era in ERAS:
        era_artists: list[Artist] = []
        for k in range(era.artists):
            country: str | None = COUNTRIES[(k + country_offset) % len(COUNTRIES)]
            if era.name == "full" and k == 0:
                country = "XW"  # non-country pseudo code, stored as-is, ignored by statistics
            if era.name == "full" and k == 1:
                country = None  # no country known
            a = Artist(f"a{idx}", names[idx], era.name, uid(f"artist:{idx}"), country, _votes(rng))
            era_artists.append(a)
            idx += 1
        country_offset += era.artists
        artists += era_artists
        for pa in era_artists:
            for al in range(rng.randint(2, 4)):
                rel_key = f"{pa.key}:al{al}"
                rg_genres = _votes(rng) if rng.random() < 0.6 else []
                for t in range(rng.randint(5, 9)):
                    credited = [pa]
                    if rng.random() < 0.18:  # collaboration
                        other = rng.choice([x for x in era_artists if x is not pa])
                        credited.append(other)
                        if rng.random() < 0.2:
                            third = rng.choice([x for x in era_artists if x not in credited])
                            credited.append(third)
                    tracks.append(
                        Track(
                            key=f"{rel_key}:t{t}",
                            title=f"Track {len(tracks):04d}",
                            era=era.name,
                            recording=uid(f"recording:{rel_key}:t{t}"),
                            release=uid(f"release:{rel_key}"),
                            release_group=uid(f"release_group:{rel_key}"),
                            release_name=f"Album {pa.key}-{al}",
                            artists=credited,
                            genres=_votes(rng, 1, 2) if rng.random() < 0.5 else [],
                            rg_genres=rg_genres,
                        )
                    )
    return artists, tracks


def _credit(artists: list[Artist]) -> tuple[str, list[dict[str, Any]]]:
    joins = [" feat. ", " & "]
    text, entries = "", []
    for i, a in enumerate(artists):
        phrase = "" if i == len(artists) - 1 else joins[min(i, 1)]
        text += a.name + phrase
        entries.append(
            {"artist_credit_name": a.name, "artist_mbid": str(a.mbid), "join_phrase": phrase}
        )
    return text, entries


def _payload(rng: random.Random, i: int, ts: int, tr: Track) -> dict[str, Any]:
    msid = uid(f"msid:{i}")
    credit, entries = _credit(tr.artists)
    roll = rng.random()
    meta: dict[str, Any] = {
        "track_name": tr.title,
        "artist_name": credit,
        "release_name": tr.release_name,
        "additional_info": {"submission_client": "synthetic-seed"},
    }
    mapping: dict[str, Any] | None = {
        "recording_mbid": str(tr.recording),
        "release_mbid": str(tr.release),
        "release_group_mbid": str(tr.release_group),
        "artist_mbids": [str(a.mbid) for a in tr.artists],
        "artists": entries,
    }
    if roll < 0.05:  # unmapped entirely, unmapped artist name
        mapping = None
        meta["artist_name"] = rng.choice(UNMAPPED_ARTISTS)
    elif roll < 0.08:  # recording mapped only; artist credit stays a bare string
        mapping = {"recording_mbid": str(tr.recording)}
    elif roll < 0.10:  # credit-only mapping without per-artist entries
        mapping = {
            "recording_mbid": str(tr.recording),
            "release_mbid": str(tr.release),
            "release_group_mbid": str(tr.release_group),
        }
    if rng.random() < 0.03:
        meta.pop("release_name")
        if mapping:
            mapping.pop("release_mbid", None)
            mapping.pop("release_group_mbid", None)
    elif rng.random() < 0.01:
        meta["track_name"] = None
    if mapping is not None:
        meta["mbid_mapping"] = mapping
    return {
        "listened_at": ts,
        "recording_msid": str(msid),
        "user_name": USER,
        "track_metadata": meta,
    }


def _invalid_rows(n: int) -> list[dict[str, Any]]:
    base_ts = int(datetime(2021, 6, 1, tzinfo=UTC).timestamp())
    rows: list[dict[str, Any]] = []
    for k in range(n):
        row: dict[str, Any] = {
            "listened_at": base_ts + k * 60,
            "recording_msid": str(uid(f"bad:{k}")),
            "user_name": USER,
            "track_metadata": {"track_name": f"Broken {k}", "artist_name": "Broken Artist"},
        }
        if k % 3 == 0:
            del row["recording_msid"]
        elif k % 3 == 1:
            row["listened_at"] = None
        else:
            row["recording_msid"] = "not-a-uuid"
        rows.append(row)
    return rows


def build_dataset() -> Dataset:
    rng = random.Random(SEED)
    artists, tracks = build_catalog(rng)
    payloads: list[dict[str, Any]] = []
    i = 0
    for era in ERAS:
        pool = [t for t in tracks if t.era == era.name]
        rng.shuffle(pool)
        weights = [1 / (r + 1) ** 0.8 for r in range(len(pool))]
        s0, s1 = int(era.start.timestamp()), int(era.end.timestamp())
        stamps = sorted(rng.sample(range(s0, s1), era.listens))
        for k in range(1, len(stamps)):
            if rng.random() < 0.01:
                stamps[k] = stamps[k - 1]  # same second, distinct msid
        stamps[0], stamps[-1] = s0, s1 - 1  # pin partition edges
        for ts, tr in zip(stamps, rng.choices(pool, weights, k=era.listens), strict=True):
            payloads.append(_payload(rng, i, ts, tr))
            i += 1
    assert len(payloads) == TOTAL_LISTENS
    ds = Dataset(artists, tracks, payloads, _invalid_rows(QUARANTINE_ROWS))
    for a in artists:
        ds.era_of[("artist", a.mbid)] = a.era
    for t in tracks:
        ds.era_of[("recording", t.recording)] = t.era
        ds.era_of[("release_group", t.release_group)] = t.era
    return ds


# ---------------------------------------------------------------- enrichment plan


def _h(mbid: uuid.UUID) -> int:
    return int(hashlib.sha256(str(mbid).encode()).hexdigest(), 16) % 100


def entity_state(era: str, etype: str, mbid: uuid.UUID, transient: bool = False) -> str:
    """'enriched' | 'transient_error' | 'none' for one entity."""
    if era == "zero":
        return "none"
    if era == "full":
        return "enriched"
    h = _h(mbid)
    if h < 50:
        return "enriched"
    if transient and etype == "recording" and h < 55:
        return "transient_error"
    return "none"


def _gv(
    votes: list[tuple[int, int]], key: str = "genre_mbid", label: str = "name"
) -> list[dict[str, Any]]:
    return [{key: str(genre_mbid(g)), label: GENRES[g], "count": c} for g, c in votes]


def _lb_recording_entry(ds: Dataset, tr: Track) -> dict[str, Any]:
    """Upstream-shaped LB metadata, normalized with the production adapter function."""

    def on(etype: str, mbid: uuid.UUID) -> bool:
        return entity_state(tr.era, etype, mbid) == "enriched"

    artist_tags = [
        {"artist_mbid": str(a.mbid), **t}
        for a in tr.artists
        if on("artist", a.mbid)
        for t in _gv(a.genres, label="tag")
    ]
    meta = {
        "recording": {"name": tr.title},
        "release": {"name": tr.release_name, "release_group_mbid": str(tr.release_group)},
        "artist": {"artists": [{"artist_mbid": str(a.mbid), "name": a.name} for a in tr.artists]},
        "tag": {
            "recording": _gv(tr.genres, label="tag") + [{"tag": "seen live", "count": 3}],
            "release_group": (
                _gv(tr.rg_genres, label="tag") if on("release_group", tr.release_group) else []
            ),
            "artist": artist_tags,
        },
    }
    return normalize_entry(str(tr.recording), meta)


@dataclass
class Plan:
    """Cache/entity writes, in application order; pure data (no DB)."""

    lookups: list[CachedLookup] = field(default_factory=list)
    completions: list[tuple[EnrichmentWork, list[Any], str | None, datetime]] = field(
        default_factory=list
    )
    retries: list[tuple[EnrichmentWork, datetime]] = field(default_factory=list)


def _lookup(
    etype: str, mbid: uuid.UUID, source: str, status: str, payload: dict[str, Any]
) -> CachedLookup:
    ttl = POLICY.positive_ttl if status == "positive" else POLICY.empty_ttl
    return CachedLookup(
        etype,  # type: ignore[arg-type]
        str(mbid),
        source,  # type: ignore[arg-type]
        status,  # type: ignore[arg-type]
        payload,
        INGESTED_AT,
        INGESTED_AT + ttl,
    )


def build_plan(ds: Dataset, include_transient: bool = False) -> Plan:
    plan = Plan()
    used = _used_entities(ds)
    seen_rg: set[uuid.UUID] = set()
    seen_art: set[uuid.UUID] = set()
    tracks = sorted(ds.tracks, key=lambda t: t.key)
    for tr in tracks:
        # recordings
        if ("recording", tr.recording) in used:
            st = entity_state(tr.era, "recording", tr.recording, include_transient)
            work = EnrichmentWork("recording", str(tr.recording), 0)
            if st == "transient_error":
                retry_at = INGESTED_AT + timedelta(seconds=POLICY.retry_base_seconds)
                plan.lookups.append(
                    CachedLookup(
                        "recording", str(tr.recording), "listenbrainz", "transient_error", {},
                        INGESTED_AT, retry_at, 1,
                    )
                )  # fmt: skip
                plan.retries.append((work, retry_at))
            elif st == "enriched":
                payload = _lb_recording_entry(ds, tr)
                votes = recognized_votes(payload)
                low = _lookup(
                    "recording",
                    tr.recording,
                    "listenbrainz",
                    "positive" if votes else "empty",
                    payload,
                )
                plan.lookups.append(low)
                plan.completions.append((work, votes, None, low.next_retry_at))
        # release groups (only non-recording-driven leftovers in full era get an empty MB lookup)
        rg = tr.release_group
        if rg not in seen_rg and ("release_group", rg) in used:
            seen_rg.add(rg)
            if entity_state(tr.era, "release_group", rg) == "enriched":
                _entity_completion(plan, "release_group", rg, tr.rg_genres, None, tr.era)
        for a in tr.artists:
            if a.mbid in seen_art or ("artist", a.mbid) not in used:
                continue
            seen_art.add(a.mbid)
            if entity_state(a.era, "artist", a.mbid) == "enriched":
                _entity_completion(plan, "artist", a.mbid, a.genres, a.country, a.era)
    return plan


def _entity_completion(
    plan: Plan,
    etype: str,
    mbid: uuid.UUID,
    genres: list[tuple[int, int]],
    country: str | None,
    era: str,
) -> None:
    work = EnrichmentWork(etype, str(mbid), 0)  # type: ignore[arg-type]
    gpayload = {"genres": _gv(genres)}
    votes = recognized_votes(gpayload)
    rows: list[CachedLookup] = []
    if votes:
        rows.append(_lookup(etype, mbid, "listenbrainz", "positive", gpayload))
    if etype == "artist":
        mb_payload: dict[str, Any] = {"country": country} if country else {}
        status = "positive" if valid_country(country) else "empty"
        rows.append(_lookup(etype, mbid, "musicbrainz", status, mb_payload))
    elif not votes:
        rows.append(_lookup(etype, mbid, "musicbrainz", "empty", {"genres": []}))
    plan.lookups += rows
    plan.completions.append(
        (work, votes, valid_country(country), min(r.next_retry_at for r in rows))
    )


def _used_entities(ds: Dataset) -> set[tuple[str, uuid.UUID]]:
    """Entities as the production normalizer extracts them (mirrors enrichment_queue refresh)."""
    used: set[tuple[str, uuid.UUID]] = set()
    for p in ds.payloads:
        m = (p["track_metadata"].get("mbid_mapping")) or {}
        for key, et in (("recording_mbid", "recording"), ("release_group_mbid", "release_group")):
            if m.get(key):
                used.add((et, uuid.UUID(m[key])))
        for a in m.get("artists") or []:
            if a.get("artist_mbid"):
                used.add(("artist", uuid.UUID(a["artist_mbid"])))
    return used


# ---------------------------------------------------------------- reporting


def dataset_digest(ds: Dataset, plan: Plan) -> str:
    h = hashlib.sha256()
    for obj in (
        ds.payloads,
        ds.invalid,
        [(r.entity_type, r.mbid, r.source, r.status, r.payload) for r in plan.lookups],
    ):
        h.update(json.dumps(obj, sort_keys=True, default=str).encode())
    return h.hexdigest()


def describe(ds: Dataset, plan: Plan) -> dict[str, Any]:
    used = _used_entities(ds)
    out: dict[str, Any] = {
        "user": USER,
        "listens": len(ds.payloads),
        "quarantined": len(ds.invalid),
    }
    out["artists"] = len(ds.artists)
    out["genres"] = len(GENRES)
    out["countries_in_catalog"] = len({a.country for a in ds.artists if a.country})
    done = {(w.entity_type, w.mbid) for w, *_ in plan.completions}
    failed = {(w.entity_type, w.mbid) for w, _ in plan.retries}
    eras = []
    for era in ERAS:
        s0, s1 = int(era.start.timestamp()), int(era.end.timestamp())
        n = sum(1 for p in ds.payloads if s0 <= p["listened_at"] < s1)
        ents: dict[str, dict[str, int]] = {}
        for et, mbid in sorted(used, key=str):
            if ds.era_of[(et, mbid)] != era.name:
                continue
            e = ents.setdefault(et, {"entities": 0, "enriched": 0, "failed": 0})
            e["entities"] += 1
            e["enriched"] += (et, str(mbid)) in done
            e["failed"] += (et, str(mbid)) in failed
        eras.append(
            {
                "name": era.name,
                "enrichment": era.enrichment,
                "from_inclusive": era.start.isoformat(),
                "to_inclusive_last_second": era.last_second.isoformat(),
                "to_exclusive": era.end.isoformat(),
                "listens": n,
                "entities": ents,
            }
        )
    out["partitions"] = eras
    out["dataset_digest"] = dataset_digest(ds, plan)
    return out


# ---------------------------------------------------------------- database


class RefusedError(RuntimeError):
    pass


def preflight(engine: Any, ds: Dataset) -> None:
    """Refuse any DB containing data that is not part of this synthetic catalog."""
    from sqlalchemy import text

    known = {str(m) for _, m in ds.era_of}
    with engine.connect() as c:
        for table, col in (("listens", "user_name"), ("raw_listens", "user_name"),
                           ("quarantined_listens", "user_name"), ("sync_state", "user_name"),
                           ("sync_runs", "user_name")):  # fmt: skip
            n = c.execute(
                text(f"SELECT count(*) FROM {table} WHERE {col} IS DISTINCT FROM :u"), {"u": USER}
            ).scalar_one()
            if n:
                raise RefusedError(f"{table} has {n} rows not owned by {USER!r}; refusing")
        for table, col in (("metadata_cache", "entity_mbid"), ("entity_genres", "entity_mbid"),
                           ("artist_countries", "artist_mbid"),
                           ("enrichment_queue", "entity_mbid")):  # fmt: skip
            for (mbid,) in c.execute(text(f"SELECT DISTINCT {col}::text FROM {table}")):
                if mbid not in known:
                    raise RefusedError(f"{table} has non-synthetic entity {mbid}; refusing")
        extra = c.execute(text("SELECT genre_mbid::text FROM genres")).scalars().all()
        stray = set(extra) - {str(genre_mbid(i)) for i in range(len(GENRES))}
        if stray:
            raise RefusedError("genres table has non-synthetic genres; refusing")


def seed(engine: Any, *, enrich: bool = True, include_transient: bool = False) -> dict[str, Any]:
    from music_dashboard.infrastructure.persistence.repositories.listening_sync import (
        SyncRepository,
    )
    from music_dashboard.infrastructure.persistence.repositories.metadata import (
        SqlMetadataRepository,
    )

    ds = build_dataset()
    plan = build_plan(ds, include_transient)
    preflight(engine, ds)
    repo = SyncRepository(engine)
    changed = 0
    for off in range(0, len(ds.payloads), BATCH):
        changed += repo.ingest(USER, ds.payloads[off : off + BATCH], "export", INGESTED_AT)
    repo.ingest(USER, ds.invalid, "export", INGESTED_AT)
    stats: dict[str, Any] = {"ingest_changed": changed}
    if enrich:
        meta = SqlMetadataRepository(engine)
        meta.refresh_queue(INGESTED_AT)
        for lk in plan.lookups:
            meta.save_lookup(lk)
        for work, retry_at in plan.retries:
            _mark_failed(engine, work, retry_at)  # absolute values: re-runs stay no-ops
        for work, votes, country, next_retry in plan.completions:
            meta.complete(work, votes, country, next_retry, INGESTED_AT)
        stats["cache_rows"] = len(plan.lookups)
        stats["completed_entities"] = len(plan.completions)
    stats["digest"] = db_digest(engine)
    return stats


def _mark_failed(engine: Any, work: EnrichmentWork, retry_at: datetime) -> None:
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE enrichment_queue SET status='failed', attempts=1, next_retry_at=:r, "
                "locked_at=NULL, updated_at=:n, last_error='upstream transient failure' "
                "WHERE entity_type=:t AND entity_mbid=CAST(:m AS uuid)"
            ),
            {"r": retry_at, "n": INGESTED_AT, "t": work.entity_type, "m": work.mbid},
        )


def db_digest(engine: Any) -> str:
    """Order-independent content hash of listens + enrichment (ignores serial ids/updated_at)."""
    from sqlalchemy import text

    queries = {
        "listens": "SELECT user_name, listened_at, recording_msid, track_name, artist_name, "
        "release_name, recording_mbid, release_mbid, release_group_mbid FROM listens",
        "artists": "SELECT l.recording_msid, l.listened_at, a.position, a.artist_name, "
        "a.artist_mbid "
        "FROM listen_artists a JOIN listens l ON l.id = a.listen_id",
        "raw": "SELECT listened_at, recording_msid, payload FROM raw_listens "
        "WHERE tombstoned_at IS NULL",
        "genres": "SELECT genre_mbid, name FROM genres",
        "entity_genres": "SELECT entity_type, entity_mbid, genre_mbid, votes FROM entity_genres",
        "countries": "SELECT artist_mbid, country_code FROM artist_countries",
        "cache": "SELECT entity_type, entity_mbid, source, status, payload, fetched_at, "
        "next_retry_at, attempts "
        "FROM metadata_cache",
        "queue": "SELECT entity_type, entity_mbid, status, attempts, next_retry_at "
        "FROM enrichment_queue",
    }
    h = hashlib.sha256()
    with engine.connect() as c:
        for name, sql in queries.items():
            rows = sorted(
                json.dumps(
                    [str(v) if not isinstance(v, dict) else v for v in r],
                    sort_keys=True,
                    default=str,
                )
                for r in c.execute(text(sql)).all()
            )
            h.update(name.encode())
            h.update("\n".join(rows).encode())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="print partitions/digest; no DB access")
    ap.add_argument("--confirm-synthetic", action="store_true", help="required to write")
    ap.add_argument("--skip-enrichment", action="store_true", help="listens only, no cache/genres")
    ap.add_argument(
        "--include-transient",
        action="store_true",
        help="also seed transient_error cache rows (NOT replayable; off by default)",
    )
    ap.add_argument("--verify", action="store_true", help="print DB content digest, no writes")
    args = ap.parse_args(argv)
    if args.plan:
        ds = build_dataset()
        print(json.dumps(describe(ds, build_plan(ds, args.include_transient)), indent=2))
        return 0
    url = os.environ.get("MUSIC_DATABASE_URL")
    if not url:
        print("MUSIC_DATABASE_URL is required (owner/writer role)", file=sys.stderr)
        return 2
    from music_dashboard.infrastructure.persistence.database import create_db_engine

    engine = create_db_engine(url)
    if args.verify:
        print(db_digest(engine))
        return 0
    if not args.confirm_synthetic:
        print("Refusing: pass --confirm-synthetic to write synthetic data", file=sys.stderr)
        return 2
    try:
        stats = seed(
            engine, enrich=not args.skip_enrichment, include_transient=args.include_transient
        )
    except RefusedError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
