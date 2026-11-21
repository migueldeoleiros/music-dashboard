"""Offline projection rebuild from stored raw listens and the metadata cache. Never uses network.

Maintenance reprojection of the LATEST cache, not recovery of last-applied metadata state.
Cache saves may precede projection completion; replay may expose those newer cached values.
Only one stored user is supported. Stop all writers before running.

One transaction under the sync (per user) and enrichment advisory locks:
  * `listens`/`listen_artists` are re-derived from non-tombstoned `raw_listens` with the exact
    sync parser (`listening_sync._parse`). Listen ids are stable (upsert); listens without an
    active raw row are removed. raw_listens, quarantine, sync_runs, sync_state, metadata_cache and
    the enrichment queue are never written.
  * `entity_genres`/`genres`/`artist_countries` are re-projected ONLY from cached positive/empty
    lookups (LB votes win, MB fallback; country from the MB artist row); unbacked rows are
    removed. If any cache row is transient_error/pending the cache no longer holds the last
    good payload, so the rebuild refuses (RebuildUnsupported) and changes nothing.
  * writers are blocked with SHARE ROW EXCLUSIVE table locks; stop the worker anyway.
  * both generations are bumped by one; `last_updated` (data freshness) is preserved.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import OperationalError

from music_dashboard.application.enrich_metadata import recognized_votes
from music_dashboard.domain.metadata.policies import valid_country

from .listening_sync import SyncRepository
from .metadata import ENRICHMENT_LOCK

_PAGE = 1000


class RebuildUnsupported(RuntimeError):
    """Stored inputs cannot reproduce the projections faithfully; nothing was changed."""


class RebuildBusy(RuntimeError):
    """A sync or enrichment run holds an advisory lock; nothing was changed."""


class RebuildRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sync = SyncRepository(engine)

    # -- locks -----------------------------------------------------------------------------
    def _users(self) -> list[str]:
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT user_name FROM raw_listens UNION SELECT user_name FROM listens "
                    "UNION SELECT user_name FROM sync_state ORDER BY 1"
                )
            ).scalars()
            return list(rows)

    @contextmanager
    def _enrichment_lock(self) -> Iterator[None]:
        conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired = False
        clean = True
        try:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": ENRICHMENT_LOCK}
                ).scalar()
            )
            if not acquired:
                raise RebuildBusy("Metadata enrichment holds its lock; retry later")
            yield
        finally:
            if acquired:
                try:
                    clean = bool(
                        conn.execute(
                            text("SELECT pg_advisory_unlock(:k)"), {"k": ENRICHMENT_LOCK}
                        ).scalar()
                    )
                except Exception:
                    clean = False
            if not clean:
                conn.invalidate()
            conn.close()

    # -- entry point -----------------------------------------------------------------------
    def rebuild(self, *, dry_run: bool = False) -> dict[str, Any]:
        users = self._users()
        if len(users) > 1:
            raise RebuildUnsupported("Rebuild supports one user; isolate this single-user database")
        with ExitStack() as stack:
            for user in users:
                if not stack.enter_context(self._sync.locked(user)):
                    raise RebuildBusy("A sync run holds a user lock; retry later")
            stack.enter_context(self._enrichment_lock())
            if self._users() != users:  # a first-time user appeared before locks were held
                raise RebuildBusy("Users changed while locking; retry later")
            with self._engine.connect() as c:
                tx = c.begin()
                try:
                    stats = self._run(c, users)
                    if dry_run:
                        tx.rollback()
                    else:
                        tx.commit()
                except BaseException:
                    tx.rollback()
                    raise
        stats["dry_run"] = dry_run
        return stats

    def _run(self, c: Connection, users: list[str]) -> dict[str, Any]:
        # Block writers before validating cache inputs (workers must also be stopped).
        c.execute(text("SET LOCAL lock_timeout = '5s'"))
        try:
            c.execute(
                text(
                    "LOCK TABLE raw_listens, metadata_cache, listens, listen_artists, "
                    "entity_genres, genres, artist_countries, data_generation, sync_state "
                    "IN SHARE ROW EXCLUSIVE MODE"
                )
            )
        except OperationalError:
            raise RebuildBusy("Tables are locked by another writer") from None
        self._require_reconstructable(c)
        # Re-read under table locks: a first-time user may have committed since discovery.
        users = list(
            c.execute(
                text(
                    "SELECT user_name FROM raw_listens UNION SELECT user_name FROM listens "
                    "UNION SELECT user_name FROM sync_state ORDER BY 1"
                )
            ).scalars()
        )
        c.execute(text("INSERT INTO data_generation (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
        c.execute(text("SELECT 1 FROM data_generation WHERE id = 1 FOR UPDATE"))
        stats: dict[str, Any] = {"users": len(users), "listens": 0, "unparseable_raw": 0}
        counts = {"inserted": 0, "updated": 0, "removed": 0, "artists_replaced": 0}
        for user in users:
            self._rebuild_listens(c, user, stats, counts)
        stats.update({f"listens_{k}": v for k, v in counts.items()})
        stats.update(self._rebuild_metadata(c))
        c.execute(
            text(
                "UPDATE data_generation SET ingestion_generation = ingestion_generation + 1, "
                "enrichment_generation = enrichment_generation + 1 WHERE id = 1"
            )
        )  # last_updated deliberately untouched: ingestion freshness is preserved
        return stats

    # -- listens ---------------------------------------------------------------------------
    @staticmethod
    def _raw_payloads(c: Connection, user: str) -> Iterator[Any]:
        last = 0
        while True:
            rows = c.execute(
                text(
                    "SELECT id, payload FROM raw_listens WHERE user_name = :u "
                    "AND tombstoned_at IS NULL AND id > :last ORDER BY id LIMIT :n"
                ),
                {"u": user, "last": last, "n": _PAGE},
            ).all()
            if not rows:
                return
            last = rows[-1][0]
            for _, payload in rows:
                yield payload

    def _rebuild_listens(
        self, c: Connection, user: str, stats: dict[str, Any], counts: dict[str, int]
    ) -> None:
        valid, invalid = SyncRepository._stage(
            c, user, self._raw_payloads(c, user), "rebuild", datetime.now().astimezone()
        )
        if invalid:
            raise RebuildUnsupported(
                "Active raw payloads cannot be parsed; restore a verified backup"
            )
        stats["listens"] += valid
        stats["unparseable_raw"] += invalid
        p = {"u": user}
        join = (
            "r.user_name = :u AND r.listened_at = s.listened_at "
            "AND r.recording_msid = s.recording_msid"
        )
        counts["removed"] += c.execute(
            text(
                "DELETE FROM listens r WHERE r.user_name = :u AND NOT EXISTS (SELECT 1 FROM "
                "snapshot_rows s WHERE s.listened_at = r.listened_at "
                "AND s.recording_msid = r.recording_msid)"
            ),
            p,
        ).rowcount
        counts["inserted"] += c.execute(
            text(
                "INSERT INTO listens (user_name, listened_at, recording_msid, track_name, "
                "artist_name, release_name, recording_mbid, release_mbid, release_group_mbid) "
                "SELECT :u, s.listened_at, s.recording_msid, s.track_name, s.artist_name, "
                "s.release_name, s.recording_mbid, s.release_mbid, s.release_group_mbid "
                "FROM snapshot_rows s ORDER BY s.listened_at, s.recording_msid "
                "ON CONFLICT (user_name, listened_at, recording_msid) DO NOTHING"
            ),
            p,
        ).rowcount
        counts["updated"] += c.execute(
            text(
                "UPDATE listens r SET track_name = s.track_name, artist_name = s.artist_name, "
                "release_name = s.release_name, recording_mbid = s.recording_mbid, "
                "release_mbid = s.release_mbid, release_group_mbid = s.release_group_mbid, "
                "updated_at = now() FROM snapshot_rows s "
                f"WHERE {join} AND (r.track_name, r.artist_name, r.release_name, "
                "r.recording_mbid, r.release_mbid, r.release_group_mbid) IS DISTINCT FROM "
                "(s.track_name, s.artist_name, s.release_name, s.recording_mbid, "
                "s.release_mbid, s.release_group_mbid)"
            ),
            p,
        ).rowcount
        c.execute(
            text(
                "INSERT INTO snapshot_artist_diff SELECT r.id, s.artists FROM snapshot_rows s "
                f"JOIN listens r ON {join} WHERE COALESCE((SELECT jsonb_agg("
                "jsonb_build_object('n', a.artist_name, 'm', a.artist_mbid) "
                "ORDER BY a.position) FROM listen_artists a WHERE a.listen_id = r.id), "
                "'[]'::jsonb) IS DISTINCT FROM s.artists"
            ),
            p,
        )
        counts["artists_replaced"] += int(
            c.execute(text("SELECT count(*) FROM snapshot_artist_diff")).scalar_one()
        )
        c.execute(
            text(
                "DELETE FROM listen_artists WHERE listen_id IN "
                "(SELECT listen_id FROM snapshot_artist_diff)"
            )
        )
        c.execute(
            text(
                "INSERT INTO listen_artists (listen_id, position, artist_name, artist_mbid) "
                "SELECT d.listen_id, (o.ord - 1)::smallint, o.e ->> 'n', (o.e ->> 'm')::uuid "
                "FROM snapshot_artist_diff d CROSS JOIN LATERAL "
                "jsonb_array_elements(d.artists) WITH ORDINALITY AS o(e, ord)"
            )
        )
        c.execute(text("DROP TABLE snapshot_rows, snapshot_invalid, snapshot_artist_diff"))

    # -- metadata --------------------------------------------------------------------------
    @staticmethod
    def _require_reconstructable(c: Connection) -> None:
        """Transient/pending cache rows overwrite the last good payload, so live projections
        may hold information the cache no longer has. Refuse rather than rebuild wrongly."""
        bad = c.execute(
            text(
                "SELECT status, count(*) FROM metadata_cache "
                "WHERE status NOT IN ('positive', 'empty') GROUP BY status ORDER BY status"
            )
        ).all()
        if bad:
            detail = ", ".join(f"{n} {s}" for s, n in bad)
            raise RebuildUnsupported(
                f"metadata_cache has rows that cannot be reconstructed ({detail}); let "
                "enrichment settle them first. Nothing was changed."
            )

    @staticmethod
    def _cache_rows(c: Connection) -> Iterator[Any]:
        last = 0
        while True:
            rows = c.execute(
                text(
                    "SELECT id, entity_type, entity_mbid, source, payload, fetched_at "
                    "FROM metadata_cache WHERE id > :last ORDER BY id LIMIT :n"
                ),
                {"last": last, "n": _PAGE},
            ).all()
            if not rows:
                return
            last = rows[-1][0]
            yield from rows

    def _rebuild_metadata(self, c: Connection) -> dict[str, int]:
        """Project strictly from cache with the EnrichMetadata precedence: LB votes win, MB
        votes are the fallback; artist country comes from the MB row. Anything the cache does
        not back is removed (atomic with the rest of the rebuild)."""
        lb: dict[tuple[str, UUID], Any] = {}
        mb: dict[tuple[str, UUID], Any] = {}
        for _, etype, mbid, source, payload, fetched_at in self._cache_rows(c):
            (lb if source == "listenbrainz" else mb)[(etype, mbid)] = (
                payload if isinstance(payload, dict) else {},
                fetched_at,
            )
        votes_rows: list[dict[str, Any]] = []
        country_rows: list[dict[str, Any]] = []
        name_candidates: dict[str, list[tuple[Any, str]]] = {}
        for key in sorted(set(lb) | set(mb), key=lambda k: (k[0], str(k[1]))):
            etype, mbid = key
            for source_rows, is_mb in ((lb, False), (mb, True)):
                if key not in source_rows:
                    continue
                payload, at = source_rows[key]
                found = recognized_votes(payload, musicbrainz=is_mb)
                if found:
                    for v in found:
                        votes_rows.append(
                            {"t": etype, "m": mbid, "g": UUID(v.mbid), "v": v.count, "f": at}
                        )
                        name_candidates.setdefault(v.mbid, []).append((at, v.name))
                    break
            if etype == "artist" and key in mb:
                country = valid_country(mb[key][0].get("country"))
                if country:
                    country_rows.append({"m": mbid, "c": country, "f": mb[key][1]})
        # newest cache row names a genre; ties break on the lexicographically smallest name
        names = {
            g: sorted(cands, key=lambda x: (x[0], [-ord(ch) for ch in x[1]]))[-1][1]
            for g, cands in name_candidates.items()
        }
        c.execute(
            text(
                "CREATE TEMP TABLE rb_eg (entity_type text, entity_mbid uuid, genre_mbid uuid, "
                "votes integer, fetched_at timestamptz) ON COMMIT DROP"
            )
        )
        c.execute(
            text(
                "CREATE TEMP TABLE rb_ac (artist_mbid uuid, country_code text, "
                "fetched_at timestamptz) ON COMMIT DROP"
            )
        )
        if votes_rows:
            c.execute(
                text("INSERT INTO rb_eg VALUES (:t, :m, :g, :v, :f)"),
                votes_rows,
            )
        if country_rows:
            c.execute(text("INSERT INTO rb_ac VALUES (:m, :c, :f)"), country_rows)
        for g, name in sorted(names.items()):
            c.execute(
                text(
                    "INSERT INTO genres (genre_mbid, name) VALUES (:g, :n) "
                    "ON CONFLICT (genre_mbid) DO UPDATE SET name = EXCLUDED.name"
                ),
                {"g": UUID(g), "n": name},
            )
        removed_eg = c.execute(
            text(
                "DELETE FROM entity_genres e WHERE NOT EXISTS (SELECT 1 FROM rb_eg t WHERE "
                "t.entity_type = e.entity_type AND t.entity_mbid = e.entity_mbid "
                "AND t.genre_mbid = e.genre_mbid AND t.votes = e.votes)"
            )
        ).rowcount
        added_eg = c.execute(
            text(
                "INSERT INTO entity_genres (entity_type, entity_mbid, genre_mbid, votes, "
                "fetched_at) SELECT t.* FROM rb_eg t WHERE NOT EXISTS (SELECT 1 FROM "
                "entity_genres e WHERE e.entity_type = t.entity_type "
                "AND e.entity_mbid = t.entity_mbid AND e.genre_mbid = t.genre_mbid)"
            )
        ).rowcount
        c.execute(
            text(
                "DELETE FROM genres g WHERE NOT EXISTS (SELECT 1 FROM entity_genres e "
                "WHERE e.genre_mbid = g.genre_mbid)"
            )
        )
        removed_ac = c.execute(
            text(
                "DELETE FROM artist_countries a WHERE NOT EXISTS (SELECT 1 FROM rb_ac t "
                "WHERE t.artist_mbid = a.artist_mbid AND t.country_code = a.country_code)"
            )
        ).rowcount
        added_ac = c.execute(
            text(
                "INSERT INTO artist_countries (artist_mbid, country_code, fetched_at) "
                "SELECT t.* FROM rb_ac t WHERE NOT EXISTS (SELECT 1 FROM artist_countries a "
                "WHERE a.artist_mbid = t.artist_mbid)"
            )
        ).rowcount
        return {
            "cache_entities": len(set(lb) | set(mb)),
            "entity_genres_removed": removed_eg,
            "entity_genres_added": added_eg,
            "artist_countries_removed": removed_ac,
            "artist_countries_added": added_ac,
        }
