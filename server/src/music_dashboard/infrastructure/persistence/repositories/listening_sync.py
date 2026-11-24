"""Sync persistence: advisory lock, checkpoints, runs, raw/normalized ingest, snapshot activation.

Domain-independent: operates on LB-shaped listen payload dicts. Identity is
(user, listened_at epoch-second, recording_msid). Rows lacking a valid identity are quarantined,
never guessed. All writes are set-based through per-transaction TEMP tables so a complete
snapshot is staged before any mutation and no full history is loaded into Python.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, text

from music_dashboard.domain.listening.entities import epoch_seconds
from music_dashboard.domain.listening.feat_credit import split_feat

from ..models import DataGeneration
from ..statistics_queries import WHITESPACE

CHECKPOINT_KEYS = (
    "cursor_max_ts",
    "last_incremental_at",
    "last_export_at",
    "ambiguous_boundary_ts",
    "requires_reconcile",
)
RUN_KINDS = ("incremental", "export", "reconcile")
RUN_STATUSES = ("running", "succeeded", "failed", "ambiguous", "aborted")
MAX_DELETE_FRACTION_PERCENT = 10
_CHUNK = 1000
_MAX_ERROR_CHARS = 1000
# Stored deadlines are untrusted history (older builds could persist datetime.max). A run's
# deadline is bounded relative to that run's own finished_at, never to "now", so an outlier
# expires 24 h after the run that wrote it instead of locking sync forever.
MAX_DEADLINE = timedelta(hours=24)
_DEADLINE_ROW_LIMIT = 500


def _bounded_deadline(raw: Any, finished_at: datetime | None) -> datetime | None:
    if not isinstance(raw, str) or finished_at is None:
        return None
    try:
        value = _aware(datetime.fromisoformat(raw))
        return min(value, _aware(finished_at) + MAX_DEADLINE)
    except (ValueError, OverflowError):
        return None


_SNAPSHOT_DDL = (
    """CREATE TEMP TABLE snapshot_rows (
        listened_at timestamptz NOT NULL,
        recording_msid uuid NOT NULL,
        payload jsonb NOT NULL,
        track_name text, artist_name text, release_name text,
        recording_mbid uuid, release_mbid uuid, release_group_mbid uuid,
        artists jsonb NOT NULL,
        changed boolean NOT NULL DEFAULT false,
        PRIMARY KEY (listened_at, recording_msid)
    ) ON COMMIT DROP""",
    """CREATE TEMP TABLE snapshot_invalid (
        reason text NOT NULL, missing_fields text[] NOT NULL, payload jsonb NOT NULL
    ) ON COMMIT DROP""",
    """CREATE TEMP TABLE snapshot_artist_diff (
        listen_id bigint PRIMARY KEY, artists jsonb NOT NULL
    ) ON COMMIT DROP""",
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


def _str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class _Parsed:
    listened_at: datetime | None = None
    msid: uuid.UUID | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    artists: list[dict[str, str | None]] = field(default_factory=list)
    reason: str = ""
    missing: list[str] = field(default_factory=list)


def _parse(payload: Any, user: str) -> _Parsed:
    """Extract identity + normalized fields. Invalid identity -> reason/missing set."""
    out = _Parsed()
    if not isinstance(payload, dict):
        out.reason = "payload is not an object"
        out.missing = ["listened_at", "recording_msid"]
        return out
    tm = _dict(payload.get("track_metadata"))
    info = _dict(tm.get("additional_info"))
    # top-level mbid_mapping (API/export) wins per key; track_metadata one is the fallback
    mapping = {**_dict(tm.get("mbid_mapping")), **_dict(payload.get("mbid_mapping"))}

    ts = epoch_seconds(payload.get("listened_at"))  # int or integral float (real export)
    if ts is not None:
        out.listened_at = datetime.fromtimestamp(ts, UTC)
    else:
        out.missing.append("listened_at")
    raw_msid = (
        payload["recording_msid"] if "recording_msid" in payload else info.get("recording_msid")
    )
    msid = _uuid(raw_msid)
    if msid is None or msid.int == 0:
        out.missing.append("recording_msid")
    else:
        out.msid = msid
    owner = payload.get("user_name")
    if owner is not None and (not isinstance(owner, str) or owner.casefold() != user.casefold()):
        out.missing.append("user_name")
    if out.missing:
        out.reason = "missing or invalid identity: " + ",".join(out.missing)
        return out

    def mbid(key: str) -> uuid.UUID | None:
        return _uuid(mapping.get(key)) or _uuid(info.get(key))

    credit = _str(tm.get("artist_name"))
    artists: list[dict[str, str | None]] = []
    mapped = mapping.get("artists")
    if isinstance(mapped, list):
        for a in mapped:
            a = _dict(a)
            name = _str(a.get("artist_credit_name")) or _str(a.get("name"))
            if name:
                m = _uuid(a.get("artist_mbid"))
                artists.append({"n": name, "m": str(m) if m else None})
    if not artists and credit:
        raw_mbids = mapping.get("artist_mbids") or info.get("artist_mbids")
        ids = [_uuid(x) for x in raw_mbids] if isinstance(raw_mbids, list) else []
        only = ids[0] if len(ids) == 1 else None
        names = split_feat(credit) if only is None else [credit]
        artists.extend({"n": n, "m": str(only) if only else None} for n in names)
    if credit is None and artists:
        credit = "".join(a["n"] or "" for a in artists)
    out.artists = artists
    out.fields = {
        "track_name": _str(tm.get("track_name")),
        "artist_name": credit,
        "release_name": _str(tm.get("release_name")),
        "recording_mbid": mbid("recording_mbid"),
        "release_mbid": mbid("release_mbid"),
        "release_group_mbid": mbid("release_group_mbid"),
    }
    return out


# Case-SENSITIVE on purpose: "LOONA" and "Loona" can be different artists, so only identical
# names (after Unicode normalization and trimming) may alias.
_EXACT = "btrim(normalize({col}, NFKC), :whitespace)"

_ALIAS_ARTISTS = """
WITH known AS (
  SELECT {fold_n} AS f, m FROM (
    {stored}
    SELECT e ->> 'n' AS n, (e ->> 'm')::uuid AS m
    FROM snapshot_rows s CROSS JOIN LATERAL jsonb_array_elements(s.artists) e
    WHERE e ->> 'm' IS NOT NULL
  ) k
), uniq AS (
  SELECT f, min(m::text) AS m FROM known
  WHERE f <> '' AND lower(f) <> 'various artists' GROUP BY f HAVING count(DISTINCT m) = 1
), fixed AS (
  SELECT s.listened_at, s.recording_msid, (
    SELECT jsonb_agg(CASE WHEN t.e ->> 'm' IS NULL AND u.m IS NOT NULL
        THEN jsonb_build_object('n', t.e ->> 'n', 'm', u.m) ELSE t.e END ORDER BY t.ord)
    FROM jsonb_array_elements(s.artists) WITH ORDINALITY AS t(e, ord)
    LEFT JOIN uniq u ON t.e ->> 'm' IS NULL AND u.f = {fold_t}
  ) AS artists
  FROM snapshot_rows s
)
UPDATE snapshot_rows s SET artists = x.artists FROM fixed x
WHERE x.listened_at = s.listened_at AND x.recording_msid = s.recording_msid
  AND x.artists IS DISTINCT FROM s.artists
"""


def alias_unmapped_artists(c: Connection, *, include_stored: bool) -> int:
    """Give a name-only credit the MBID of an artist with that exact name, but only when exactly
    one MBID is known for it (other stored listens and/or this batch; "Various Artists" never
    aliases). Runs on the staged rows so the artist diff stays stable. The raw payload is
    untouched; a rebuild re-derives it from raw alone (`include_stored=False`)."""
    stored = (
        "SELECT a.artist_name AS n, a.artist_mbid AS m FROM listen_artists a "
        "JOIN listens l ON l.id = a.listen_id WHERE a.artist_mbid IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM snapshot_rows s WHERE s.listened_at = l.listened_at "
        "AND s.recording_msid = l.recording_msid) UNION"
        if include_stored
        else ""
    )
    sql = _ALIAS_ARTISTS.format(
        stored=stored,
        fold_n=_EXACT.format(col="n"),
        fold_t=_EXACT.format(col="t.e ->> 'n'"),
    )
    return c.execute(text(sql), {"whitespace": WHITESPACE}).rowcount


_API_MBID_KEYS = ("recording_mbid", "release_mbid", "release_group_mbid", "artist_mbids")


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fill(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """existing values fill keys the incoming dict omits or leaves null/empty."""
    out = dict(incoming)
    for k, v in existing.items():
        if not _present(out.get(k)) and _present(v):
            out[k] = v
    return out


def _merge_api(incoming: Any, existing: Any) -> Any:
    """API observations are NON-authoritative for enrichment: omitted/null/empty mapping,
    credits and MBIDs never erase what a stored payload (export or API) already holds;
    non-empty incoming values still override (a real remap). Only exports clear."""
    if not isinstance(incoming, dict) or not isinstance(existing, dict):
        return incoming
    out = dict(incoming)
    ex_tm, in_tm = _dict(existing.get("track_metadata")), _dict(incoming.get("track_metadata"))
    tm = dict(in_tm)
    for holder_in, holder_ex, target in (
        (in_tm, ex_tm, tm),
        (incoming, existing, out),
    ):
        if _present(holder_ex.get("mbid_mapping")):
            target["mbid_mapping"] = _fill(
                _dict(holder_in.get("mbid_mapping")), _dict(holder_ex["mbid_mapping"])
            )
    info = _fill(
        _dict(in_tm.get("additional_info")),
        {k: v for k, v in _dict(ex_tm.get("additional_info")).items() if k in _API_MBID_KEYS},
    )
    if info:
        tm["additional_info"] = info
    if tm or "track_metadata" in out:
        out["track_metadata"] = tm
    return out


def _json_clean(stats: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = json.loads(json.dumps(stats or {}, default=str))
    return cleaned


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        raise ValueError("snapshot row is not JSON serializable") from None


class SyncRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._held_guard = threading.Lock()
        self._held: dict[str, list[int]] = {}  # user -> [owner thread id, depth]

    # -- locking ---------------------------------------------------------------------------
    @staticmethod
    def lock_key(user: str) -> int:
        digest = hashlib.sha256(f"music-dashboard:sync:{user}".encode()).digest()
        return int.from_bytes(digest[:8], "big", signed=True)

    @contextmanager
    def locked(self, user: str) -> Iterator[bool]:
        """Session advisory lock on a dedicated connection; yields False if already held.

        Reentrant for the same thread on this instance: nested calls only bump a counter
        (no DB reacquire) and yield True; the DB lock is released with the outermost exit.
        """
        me = threading.get_ident()
        with self._held_guard:
            held = self._held.get(user)
            if held is not None and held[0] == me:
                held[1] += 1
                nested = True
            else:
                nested = False
        if nested:
            try:
                yield True
            finally:
                with self._held_guard:
                    self._held[user][1] -= 1
            return
        key = self.lock_key(user)
        conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired = False
        try:
            acquired = bool(
                conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
            )
            if acquired:
                with self._held_guard:
                    self._held[user] = [me, 1]
            yield acquired
        finally:
            if acquired:
                with self._held_guard:
                    self._held.pop(user, None)
            clean = True
            if acquired:
                try:
                    clean = bool(
                        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key}).scalar()
                    )
                except Exception:
                    clean = False
            if clean:
                conn.close()
            else:  # never return a connection that may still hold the lock to the pool
                conn.invalidate()
                conn.close()

    # -- checkpoint ------------------------------------------------------------------------
    def load_checkpoint(self, user: str) -> dict[str, Any]:
        with self._engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT cursor_max_ts, last_incremental_at, last_export_at, "
                    "ambiguous_boundary_ts, requires_reconcile FROM sync_state "
                    "WHERE user_name = :u"
                ),
                {"u": user},
            ).first()
        if row is None:
            return {
                "cursor_max_ts": None,
                "last_incremental_at": None,
                "last_export_at": None,
                "ambiguous_boundary_ts": None,
                "requires_reconcile": False,
            }
        return {
            "cursor_max_ts": row[0],
            "last_incremental_at": row[1],
            "last_export_at": row[2],
            "ambiguous_boundary_ts": row[3],
            "requires_reconcile": bool(row[4]),
        }

    def save_checkpoint(self, user: str, values: dict[str, Any]) -> None:
        """Partial update: only supplied keys change (None clears nullable ones)."""
        unknown = set(values) - set(CHECKPOINT_KEYS)
        if unknown:
            raise ValueError(f"unknown checkpoint keys: {sorted(unknown)}")
        vals = dict(values)
        if "requires_reconcile" in vals:
            vals["requires_reconcile"] = bool(vals["requires_reconcile"])
        for k in ("last_incremental_at", "last_export_at"):
            if isinstance(vals.get(k), datetime):
                vals[k] = _aware(vals[k])
        cols = list(vals)
        insert_cols = ", ".join(["user_name", *cols])
        insert_vals = ", ".join([":user_name", *(f":{k}" for k in cols)])
        sets = ", ".join([*(f"{k} = EXCLUDED.{k}" for k in cols), "updated_at = now()"])
        with self._engine.begin() as c:
            c.execute(
                text(
                    f"INSERT INTO sync_state ({insert_cols}) VALUES ({insert_vals}) "
                    f"ON CONFLICT (user_name) DO UPDATE SET {sets}"
                ),
                {"user_name": user, **vals},
            )

    # -- runs ------------------------------------------------------------------------------
    def begin_run(self, user: str, kind: str, stats: dict[str, Any] | None = None) -> int:
        if kind not in RUN_KINDS:
            raise ValueError(f"invalid run kind: {kind}")
        with self._engine.begin() as c:
            # a still-"running" row for this user/kind is stale (callers hold the sync lock)
            c.execute(
                text(
                    "UPDATE sync_runs SET status = 'aborted', finished_at = now() "
                    "WHERE user_name = :u AND kind = :k AND status = 'running'"
                ),
                {"u": user, "k": kind},
            )
            return int(
                c.execute(
                    text(
                        "INSERT INTO sync_runs (kind, status, user_name, stats) "
                        "VALUES (:k, 'running', :u, CAST(:st AS jsonb)) RETURNING id"
                    ),
                    {"k": kind, "u": user, "st": json.dumps(_json_clean(stats))},
                ).scalar_one()
            )

    def update_run_stats(self, run_id: int, stats: dict[str, Any]) -> None:
        """Durably merge top-level keys into a run's stats (e.g. resumable scan state)."""
        with self._engine.begin() as c:
            n = c.execute(
                text("UPDATE sync_runs SET stats = stats || CAST(:st AS jsonb) WHERE id = :id"),
                {"st": json.dumps(_json_clean(stats)), "id": run_id},
            ).rowcount
        if n != 1:
            raise ValueError(f"unknown sync run: {run_id}")

    def load_scan(self, user: str) -> dict[str, Any] | None:
        """`stats.scan` of the latest incremental run, only if it is running or failed."""
        with self._engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT status, stats -> 'scan' FROM sync_runs "
                    "WHERE user_name = :u AND kind = 'incremental' ORDER BY id DESC LIMIT 1"
                ),
                {"u": user},
            ).first()
        if row is None or row[0] not in ("running", "failed") or not isinstance(row[1], dict):
            return None
        return dict(row[1])

    def finish_run(
        self,
        run_id: int,
        status: str,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in RUN_STATUSES or status == "running":
            raise ValueError(f"invalid final run status: {status}")
        clean = _json_clean(stats)
        err = error.strip()[:_MAX_ERROR_CHARS] if error else None
        with self._engine.begin() as c:
            n = c.execute(
                text(
                    "UPDATE sync_runs SET status = :s, finished_at = now(), "
                    "stats = stats || CAST(:st AS jsonb), error = :e WHERE id = :id"
                ),
                {"s": status, "st": json.dumps(clean), "e": err, "id": run_id},
            ).rowcount
        if n != 1:
            raise ValueError(f"unknown sync run: {run_id}")

    def next_retry(self, user: str, kind: str | None = None) -> datetime | None:
        """`stats.next_retry_at` (ISO string) of the user's latest finished run, else None."""
        sql = (
            "SELECT stats ->> 'next_retry_at', finished_at FROM sync_runs "
            "WHERE user_name = :u AND finished_at IS NOT NULL"
            + (" AND kind = :k" if kind else "")
            + " ORDER BY id DESC LIMIT 1"
        )
        with self._engine.connect() as c:
            row = c.execute(text(sql), {"u": user, "k": kind}).first()
        return _bounded_deadline(row[0], row[1]) if row else None

    def rate_limit_until(self, user: str) -> datetime | None:
        """Longest outstanding upstream deadline across both sync job kinds.

        Each stored deadline is clamped to its run's finished_at + 24 h. Only runs finished
        within that window can still matter, so the scan is range-limited and row-capped
        instead of transferring every matching row ever written (sync_runs is small and
        user-scoped; no index is warranted).
        """
        with self._engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT stats ->> 'rate_limit_until', finished_at FROM sync_runs "
                    "WHERE user_name = :u AND finished_at IS NOT NULL "
                    "AND finished_at > now() - make_interval(secs => :win) "
                    "AND stats ? 'rate_limit_until' ORDER BY id DESC LIMIT :lim"
                ),
                {"u": user, "win": MAX_DEADLINE.total_seconds(), "lim": _DEADLINE_ROW_LIMIT},
            ).all()
        parsed = [d for raw, fin in rows if (d := _bounded_deadline(raw, fin)) is not None]
        return max(parsed, default=None)

    # -- ingest ----------------------------------------------------------------------------
    def ingest(
        self, user: str, payloads: list[dict[str, Any]], source: str, received_at: datetime
    ) -> int:
        """Upsert raw + normalized listens; quarantine bad identity. Returns changed listens."""
        received_at = _aware(received_at)
        with self._engine.begin() as c:
            if source != "export":
                payloads = self._merge_existing(c, user, payloads)
            self._stage(c, user, payloads, source, received_at)
            counts = self._apply(c, user, source, received_at)
            if counts["changed"]:
                self._bump_generation(c)
        return counts["changed"]

    @staticmethod
    def _merge_existing(c: Connection, user: str, payloads: list[Any]) -> list[Any]:
        """Apply non-authoritative API merge against stored raw payloads (see _merge_api)."""
        keyed: list[tuple[int, datetime, uuid.UUID]] = []
        for i, pl in enumerate(payloads):
            p = _parse(pl, user)
            if p.listened_at is not None and p.msid is not None and not p.missing:
                keyed.append((i, p.listened_at, p.msid))
        out = list(payloads)
        for off in range(0, len(keyed), _CHUNK):
            chunk = keyed[off : off + _CHUNK]
            rows = c.execute(
                text(
                    "SELECT r.listened_at, r.recording_msid, r.payload FROM raw_listens r "
                    "WHERE r.user_name = :u AND (r.listened_at, r.recording_msid) IN "
                    "(SELECT * FROM unnest(CAST(:ts AS timestamptz[]), CAST(:ms AS uuid[])))"
                ),
                {"u": user, "ts": [k[1] for k in chunk], "ms": [k[2] for k in chunk]},
            ).all()
            stored = {(r[0], r[1]): r[2] for r in rows}
            for i, at, ms in chunk:
                old = stored.get((at, ms))
                if old is not None:
                    out[i] = _merge_api(payloads[i], old)
        return out

    # -- snapshot --------------------------------------------------------------------------
    def activate_snapshot(
        self,
        user: str,
        rows: Iterable[dict[str, Any]],
        snapshot_at: datetime,
        allow_large_delete: bool = False,
        snapshot_complete: bool = False,
    ) -> dict[str, int]:
        """Stage the complete export, then upsert and tombstone atomically.

        Raises ValueError (no mutation) if staging fails or deletions exceed 10% of the
        in-scope active listens without `allow_large_delete`. Empty snapshot or any invalid
        identity blocks every deletion (`deletions_blocked` = 1), and so does
        `snapshot_complete=False` (default): only a caller holding validated evidence that the
        export is complete may pass True; quarantine/upserts still run.
        """
        snapshot_at = _aware(snapshot_at)
        with self._engine.begin() as c:
            valid, invalid = self._stage(c, user, rows, "export", snapshot_at)
            scope = {"u": user, "snap": snapshot_at}
            candidates_sql = (
                "WHERE r.user_name = :u AND r.tombstoned_at IS NULL "
                "AND r.listened_at <= :snap AND r.fetched_at <= :snap "
                "AND NOT EXISTS (SELECT 1 FROM snapshot_rows s "
                "WHERE s.listened_at = r.listened_at AND s.recording_msid = r.recording_msid)"
            )
            blocked = not snapshot_complete or valid == 0 or invalid > 0
            to_delete = 0
            if not blocked:
                to_delete = int(
                    c.execute(
                        text(f"SELECT count(*) FROM raw_listens r {candidates_sql}"), scope
                    ).scalar_one()
                )
                in_scope = int(
                    c.execute(
                        text(
                            "SELECT count(*) FROM raw_listens WHERE user_name = :u "
                            "AND tombstoned_at IS NULL AND listened_at <= :snap "
                            "AND fetched_at <= :snap"
                        ),
                        scope,
                    ).scalar_one()
                )
                if (
                    to_delete * 100 > in_scope * MAX_DELETE_FRACTION_PERCENT
                    and not allow_large_delete
                ):
                    raise ValueError(
                        f"snapshot would tombstone {to_delete} of {in_scope} listens "
                        f"(over {MAX_DELETE_FRACTION_PERCENT}%); explicit override required"
                    )
            counts = self._apply(c, user, "export", snapshot_at)
            deleted = 0
            if to_delete:
                deleted = c.execute(
                    text(
                        f"WITH k AS (UPDATE raw_listens r SET tombstoned_at = :snap, "
                        f"tombstone_reason = 'upstream_deletion' {candidates_sql} "
                        "RETURNING r.listened_at, r.recording_msid) "
                        "DELETE FROM listens l USING k WHERE l.user_name = :u "
                        "AND l.listened_at = k.listened_at AND l.recording_msid = k.recording_msid"
                    ),
                    scope,
                ).rowcount
            if counts["changed"] or to_delete:
                self._bump_generation(c)
        return {
            "rows": valid,
            "invalid": invalid,
            "quarantined": counts["quarantined"],
            "inserted": counts["inserted"],
            "updated": counts["updated"],
            "revived": counts["revived"],
            "changed": counts["changed"],
            "tombstoned": to_delete,
            "deleted_listens": deleted,
            "deletions_blocked": int(blocked),
        }

    # -- internals -------------------------------------------------------------------------
    @staticmethod
    def _bump_generation(c: Connection) -> None:
        table = DataGeneration.__tablename__
        c.execute(
            text(
                f"INSERT INTO {table} (id, ingestion_generation) VALUES (1, 1) "
                "ON CONFLICT (id) DO UPDATE SET "
                f"ingestion_generation = {table}.ingestion_generation + 1, last_updated = now()"
            )
        )

    @staticmethod
    def _stage(
        c: Connection, user: str, rows: Iterable[Any], source: str, fetched_at: datetime
    ) -> tuple[int, int]:
        """Parse everything into TEMP tables. Returns (valid, invalid) row counts."""
        for ddl in _SNAPSHOT_DDL:
            c.execute(text(ddl))
        good: list[dict[str, Any]] = []
        bad: list[dict[str, Any]] = []
        valid = invalid = 0
        ins_good = text(
            "INSERT INTO snapshot_rows (listened_at, recording_msid, payload, track_name, "
            "artist_name, release_name, recording_mbid, release_mbid, release_group_mbid, "
            "artists) VALUES (:listened_at, :msid, CAST(:payload AS jsonb), :track_name, "
            ":artist_name, :release_name, :recording_mbid, :release_mbid, :release_group_mbid, "
            "CAST(:artists AS jsonb)) ON CONFLICT (listened_at, recording_msid) DO UPDATE SET "
            "payload = EXCLUDED.payload, track_name = EXCLUDED.track_name, "
            "artist_name = EXCLUDED.artist_name, release_name = EXCLUDED.release_name, "
            "recording_mbid = EXCLUDED.recording_mbid, release_mbid = EXCLUDED.release_mbid, "
            "release_group_mbid = EXCLUDED.release_group_mbid, artists = EXCLUDED.artists"
        )
        ins_bad = text(
            "INSERT INTO snapshot_invalid (reason, missing_fields, payload) "
            "VALUES (:reason, :missing, CAST(:payload AS jsonb))"
        )

        def flush() -> None:
            if good:
                c.execute(ins_good, good)
                good.clear()
            if bad:
                c.execute(ins_bad, bad)
                bad.clear()

        for payload in rows:
            p = _parse(payload, user)
            body = _dumps(payload)
            if p.listened_at is None or p.msid is None or p.missing:
                invalid += 1
                bad.append({"reason": p.reason, "missing": p.missing, "payload": body})
            else:
                valid += 1
                good.append(
                    {
                        "listened_at": p.listened_at,
                        "msid": p.msid,
                        "payload": body,
                        "artists": json.dumps(p.artists),
                        **p.fields,
                    }
                )
            if len(good) + len(bad) >= _CHUNK:
                flush()
        flush()
        alias_unmapped_artists(c, include_stored=source != "rebuild")
        c.execute(text("ANALYZE snapshot_rows"))
        return valid, invalid

    @staticmethod
    def _apply(c: Connection, user: str, source: str, fetched_at: datetime) -> dict[str, int]:
        """Upsert staged rows into raw + normalized tables; quarantine staged invalid rows."""
        prov = json.dumps({"source": source, "fetched_at": fetched_at.isoformat()})
        p = {"u": user, "prov": prov, "fa": fetched_at}
        join = (
            "r.user_name = :u AND r.listened_at = s.listened_at "
            "AND r.recording_msid = s.recording_msid"
        )

        def flagged(sql: str) -> int:
            """Run a data-modifying statement RETURNING keys; flag staged rows changed."""
            return c.execute(
                text(
                    f"WITH k AS ({sql}) UPDATE snapshot_rows s SET changed = true FROM k "
                    "WHERE s.listened_at = k.listened_at AND s.recording_msid = k.recording_msid"
                ),
                p,
            ).rowcount

        revived = flagged(
            "UPDATE raw_listens r SET tombstoned_at = NULL, tombstone_reason = NULL, "
            "payload = s.payload, provenance = CAST(:prov AS jsonb), "
            "fetched_at = GREATEST(r.fetched_at, :fa) FROM snapshot_rows s "
            f"WHERE {join} AND r.tombstoned_at IS NOT NULL "
            "RETURNING r.listened_at, r.recording_msid"
        )
        updated = flagged(
            "UPDATE raw_listens r SET payload = s.payload, "
            "provenance = CAST(:prov AS jsonb), fetched_at = GREATEST(r.fetched_at, :fa) "
            f"FROM snapshot_rows s WHERE {join} AND r.tombstoned_at IS NULL "
            "AND r.payload IS DISTINCT FROM s.payload RETURNING r.listened_at, r.recording_msid"
        )
        # unchanged rows are still "seen now": protects them from stale tombstoning
        c.execute(
            text(
                "UPDATE raw_listens r SET fetched_at = :fa FROM snapshot_rows s "
                f"WHERE {join} AND r.fetched_at < :fa"
            ),
            p,
        )
        inserted = flagged(
            "INSERT INTO raw_listens (user_name, listened_at, recording_msid, payload, "
            "provenance, fetched_at) SELECT :u, s.listened_at, s.recording_msid, s.payload, "
            "CAST(:prov AS jsonb), :fa FROM snapshot_rows s "
            "ON CONFLICT (user_name, listened_at, recording_msid) DO NOTHING "
            "RETURNING listened_at, recording_msid"
        )
        flagged(
            "INSERT INTO listens (user_name, listened_at, recording_msid, track_name, "
            "artist_name, release_name, recording_mbid, release_mbid, release_group_mbid) "
            "SELECT :u, s.listened_at, s.recording_msid, s.track_name, s.artist_name, "
            "s.release_name, s.recording_mbid, s.release_mbid, s.release_group_mbid "
            "FROM snapshot_rows s "
            "ON CONFLICT (user_name, listened_at, recording_msid) DO NOTHING "
            "RETURNING listened_at, recording_msid"
        )
        flagged(
            "UPDATE listens r SET track_name = s.track_name, artist_name = s.artist_name, "
            "release_name = s.release_name, recording_mbid = s.recording_mbid, "
            "release_mbid = s.release_mbid, release_group_mbid = s.release_group_mbid, "
            "updated_at = now() FROM snapshot_rows s "
            f"WHERE {join} AND (r.track_name, r.artist_name, r.release_name, r.recording_mbid, "
            "r.release_mbid, r.release_group_mbid) IS DISTINCT FROM (s.track_name, "
            "s.artist_name, s.release_name, s.recording_mbid, s.release_mbid, "
            "s.release_group_mbid) RETURNING r.listened_at, r.recording_msid"
        )
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
        c.execute(
            text(
                "UPDATE snapshot_rows s SET changed = true FROM listens r "
                f"JOIN snapshot_artist_diff d ON d.listen_id = r.id WHERE {join}"
            ),
            p,
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
        quarantined = c.execute(
            text(
                "INSERT INTO quarantined_listens (user_name, reason, missing_fields, payload, "
                "provenance, fetched_at) SELECT DISTINCT :u, i.reason, i.missing_fields, "
                "i.payload, CAST(:prov AS jsonb), :fa FROM snapshot_invalid i WHERE NOT EXISTS "
                "(SELECT 1 FROM quarantined_listens q WHERE q.user_name = :u "
                "AND q.resolved_at IS NULL AND q.payload = i.payload AND q.reason = i.reason)"
            ),
            p,
        ).rowcount
        changed = int(
            c.execute(text("SELECT count(*) FROM snapshot_rows WHERE changed")).scalar_one()
        )
        return {
            "revived": revived,
            "updated": updated,
            "inserted": inserted,
            "quarantined": quarantined,
            "changed": changed,
        }
