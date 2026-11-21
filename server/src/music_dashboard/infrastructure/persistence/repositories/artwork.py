"""Artwork persistence (writer role). The API role only reads image_url via statistics joins.

Two stores, both URL-only: `artwork` (exact MBID keys) and `artwork_name` (stable `name:` ranking
keys of entities without an MBID). Nothing here ever writes to listens or raw_listens."""

from datetime import datetime
from typing import Literal, cast

from sqlalchemy import Connection, Engine, text

from music_dashboard.domain.artwork.entities import ArtworkEntityType, ArtworkOutcome, ArtworkWork
from music_dashboard.domain.artwork.matching import ArtworkNames
from music_dashboard.infrastructure.artwork.urls import CAA_ID, MBID
from music_dashboard.infrastructure.persistence.statistics_queries import CASEFOLD, WHITESPACE

# Session-level advisory lock, like ENRICHMENT_LOCK.
ARTWORK_LOCK = 730005

# Most-listened release within the release group / for the recording; ties: smallest mbid.
# `ents` has MBID-keyed entities (mbid set) and name-only ranking entities (name_key set, the
# exact `name:...` key the statistics queries produce; names are folded with the same SQL).
DUE_SQL = """
WITH raw_names AS (
  SELECT 'album'::text AS et, artist_name AS an, release_name AS tn, NULL::text AS rn,
         count(*) AS c
  FROM listens WHERE release_group_mbid IS NULL AND release_mbid IS NULL
    AND release_name IS NOT NULL GROUP BY artist_name, release_name
  UNION ALL
  SELECT 'track', artist_name, track_name, release_name, count(*) FROM listens
  WHERE recording_mbid IS NULL AND track_name IS NOT NULL
  GROUP BY artist_name, track_name, release_name
  UNION ALL
  SELECT 'artist', artist_name, NULL, NULL, count(DISTINCT listen_id) FROM listen_artists
  WHERE artist_mbid IS NULL GROUP BY artist_name
  UNION ALL
  SELECT 'artist', l.artist_name, NULL, NULL, count(*) FROM listens l
  WHERE l.artist_name IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id = l.id)
  GROUP BY l.artist_name
), folded AS MATERIALIZED (
  SELECT name, coalesce((SELECT string_agg(
    coalesce(CAST(:casefold AS jsonb)->>ch, ch), '' ORDER BY ord)
    FROM regexp_split_to_table(btrim(normalize(name, NFKC), :whitespace), '')
         WITH ORDINALITY AS chars(ch, ord)), '') AS f
  FROM (SELECT an AS name FROM raw_names UNION SELECT tn FROM raw_names) n
  WHERE name IS NOT NULL
), keyed AS (
  SELECT g.et, g.an, g.tn, g.rn, g.c,
    CASE g.et WHEN 'artist' THEN 'name:' || fa.f
      ELSE 'name:' || jsonb_build_array(coalesce(fa.f, ''), coalesce(ft.f, ''))::text END AS k
  FROM raw_names g LEFT JOIN folded fa ON fa.name = g.an LEFT JOIN folded ft ON ft.name = g.tn
  WHERE CASE g.et WHEN 'artist' THEN coalesce(fa.f, '') <> '' ELSE coalesce(ft.f, '') <> '' END
), name_ents AS (
  SELECT DISTINCT ON (et, k) et, k, sum(c) OVER (PARTITION BY et, k) AS listens, an, tn, rn
  FROM keyed ORDER BY et, k, c DESC, an, tn, rn
), ents AS (
  SELECT 'album'::text AS entity_type, release_group_mbid AS mbid, NULL::text AS name_key,
         count(*) AS listens, 'release-group'::text AS kind,
         NULL::text AS an, NULL::text AS tn, NULL::text AS rn
  FROM listens WHERE release_group_mbid IS NOT NULL GROUP BY release_group_mbid
  UNION ALL
  -- Album ranking key is coalesce(release_group, release): release-only albums need artwork too.
  SELECT 'album', release_mbid, NULL, count(*), 'release', NULL, NULL, NULL FROM listens
  WHERE release_group_mbid IS NULL AND release_mbid IS NOT NULL GROUP BY release_mbid
  UNION ALL
  SELECT 'track', recording_mbid, NULL, count(*), NULL, NULL, NULL, NULL FROM listens
  WHERE recording_mbid IS NOT NULL GROUP BY recording_mbid
  UNION ALL
  SELECT 'artist', artist_mbid, NULL, count(DISTINCT listen_id), NULL, NULL, NULL, NULL
  FROM listen_artists WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid
  UNION ALL
  SELECT et, NULL, k, listens, NULL, an, tn, rn FROM name_ents
), due AS (
  SELECT e.* FROM ents e
  LEFT JOIN artwork a ON e.mbid IS NOT NULL AND a.entity_type = e.entity_type
    AND a.entity_mbid = e.mbid
  LEFT JOIN artwork_name n ON e.mbid IS NULL AND n.entity_type = e.entity_type
    AND n.name_key = e.name_key
  WHERE CASE WHEN e.mbid IS NOT NULL
        THEN a.entity_mbid IS NULL OR a.next_retry_at IS NULL OR a.next_retry_at <= :now
        ELSE n.name_key IS NULL OR n.next_retry_at IS NULL OR n.next_retry_at <= :now END
  ORDER BY e.listens DESC, e.entity_type, e.mbid, e.name_key
  LIMIT :limit
)
SELECT d.entity_type, d.mbid::text AS mbid, d.name_key, d.listens, d.kind, d.an, d.tn, d.rn,
  CASE d.entity_type
    WHEN 'album' THEN CASE d.kind WHEN 'release' THEN d.mbid::text ELSE (
      SELECT x.release_mbid::text FROM listens x
      WHERE x.release_group_mbid = d.mbid AND x.release_mbid IS NOT NULL
      GROUP BY x.release_mbid ORDER BY count(*) DESC, x.release_mbid LIMIT 1) END
    WHEN 'track' THEN (
      SELECT x.release_mbid::text FROM listens x
      WHERE x.recording_mbid = d.mbid AND x.release_mbid IS NOT NULL
      GROUP BY x.release_mbid ORDER BY count(*) DESC, x.release_mbid LIMIT 1)
  END AS release_mbid,
  -- Exact LB-supplied CAA image (caa_release_mbid + numeric caa_id), most frequent first.
  (SELECT (m.mm->>'caa_release_mbid') || ' ' || (m.mm->>'caa_id')
   FROM listens x
   JOIN raw_listens r ON r.user_name = x.user_name AND r.listened_at = x.listened_at
     AND r.recording_msid = x.recording_msid AND r.tombstoned_at IS NULL
   CROSS JOIN LATERAL (SELECT r.payload->'track_metadata'->'mbid_mapping' AS mm) m
   WHERE jsonb_typeof(m.mm) = 'object' AND jsonb_typeof(m.mm->'caa_id') = 'number'
     AND (m.mm->>'caa_id') ~ '^[0-9]{1,15}$'
     AND (m.mm->>'caa_release_mbid') ~ '^[0-9a-fA-F-]{36}$'
     AND CASE
       WHEN d.mbid IS NULL THEN false
       WHEN d.entity_type = 'track' THEN x.recording_mbid = d.mbid
       WHEN d.entity_type = 'album' AND d.kind = 'release' THEN
         x.release_mbid = d.mbid AND x.release_group_mbid IS NULL
       WHEN d.entity_type = 'album' THEN x.release_group_mbid = d.mbid
       ELSE false END
   GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1) AS caa_hint
FROM due d ORDER BY d.listens DESC, d.entity_type, d.mbid, d.name_key
"""

# Names of MBID-keyed entities, from stored listens (read-only), most frequent first.
NAMES_SQL = {
    "album": """
SELECT 'title' AS part, release_name AS name, count(*) AS n FROM listens
WHERE {cond} AND release_name IS NOT NULL GROUP BY 2
UNION ALL SELECT 'credit', artist_name, count(*) FROM listens
WHERE {cond} AND artist_name IS NOT NULL GROUP BY 2
UNION ALL SELECT 'artist', a.artist_name, count(*) FROM listens x
JOIN listen_artists a ON a.listen_id = x.id WHERE {xcond} GROUP BY 2
""",
    "track": """
SELECT 'title', track_name, count(*) FROM listens
WHERE recording_mbid = CAST(:m AS uuid) AND track_name IS NOT NULL GROUP BY 2
UNION ALL SELECT 'release', release_name, count(*) FROM listens
WHERE recording_mbid = CAST(:m AS uuid) AND release_name IS NOT NULL GROUP BY 2
UNION ALL SELECT 'credit', artist_name, count(*) FROM listens
WHERE recording_mbid = CAST(:m AS uuid) AND artist_name IS NOT NULL GROUP BY 2
UNION ALL SELECT 'artist', a.artist_name, count(*) FROM listens x
JOIN listen_artists a ON a.listen_id = x.id
WHERE x.recording_mbid = CAST(:m AS uuid) GROUP BY 2
""",
    "artist": """
SELECT 'title', artist_name, count(*) FROM listen_artists
WHERE artist_mbid = CAST(:m AS uuid) GROUP BY 2
""",
}
_ALBUM_CONDS = {
    "release-group": (
        "release_group_mbid = CAST(:m AS uuid)",
        "x.release_group_mbid = CAST(:m AS uuid)",
    ),
    "release": (
        "release_group_mbid IS NULL AND release_mbid = CAST(:m AS uuid)",
        "x.release_group_mbid IS NULL AND x.release_mbid = CAST(:m AS uuid)",
    ),
}

UPSERT_FINAL = """
INSERT INTO artwork (entity_type, entity_mbid, source, image_url, status,
                     fetched_at, next_retry_at, attempts, match_kind, provider_ref)
VALUES (:t, CAST(:m AS uuid), :source, :url, :status, :now, :retry, 0, :kind, :ref)
ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET
  source = EXCLUDED.source, image_url = EXCLUDED.image_url, status = EXCLUDED.status,
  fetched_at = EXCLUDED.fetched_at, next_retry_at = EXCLUDED.next_retry_at, attempts = 0,
  match_kind = EXCLUDED.match_kind, provider_ref = EXCLUDED.provider_ref
"""

# A refresh failure never discards an already-resolved image: only the retry schedule moves.
UPSERT_TRANSIENT = """
INSERT INTO artwork (entity_type, entity_mbid, source, image_url, status,
                     fetched_at, next_retry_at, attempts)
VALUES (:t, CAST(:m AS uuid), 'none', NULL, 'transient', :now, :retry, 1)
ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET
  next_retry_at = EXCLUDED.next_retry_at, attempts = artwork.attempts + 1
"""

UPSERT_NAME_FINAL = """
INSERT INTO artwork_name (entity_type, name_key, source, image_url, status,
                          fetched_at, next_retry_at, attempts, match_kind, provider_ref)
VALUES (:t, :m, :source, :url, :status, :now, :retry, 0, :kind, :ref)
ON CONFLICT (entity_type, name_key) DO UPDATE SET
  source = EXCLUDED.source, image_url = EXCLUDED.image_url, status = EXCLUDED.status,
  fetched_at = EXCLUDED.fetched_at, next_retry_at = EXCLUDED.next_retry_at, attempts = 0,
  match_kind = EXCLUDED.match_kind, provider_ref = EXCLUDED.provider_ref
"""

UPSERT_NAME_TRANSIENT = """
INSERT INTO artwork_name (entity_type, name_key, source, image_url, status,
                          fetched_at, next_retry_at, attempts)
VALUES (:t, :m, 'none', NULL, 'transient', :now, :retry, 1)
ON CONFLICT (entity_type, name_key) DO UPDATE SET
  next_retry_at = EXCLUDED.next_retry_at, attempts = artwork_name.attempts + 1
"""

MAX_NAMES = 4


def _name_only(
    entity_type: str, artist: str | None, title: str | None, release: str | None
) -> ArtworkNames:
    if entity_type == "artist":
        return ArtworkNames(artist)
    artists = (artist,) if artist and artist.strip() else ()
    return ArtworkNames(title, artists, (), release if entity_type == "track" else None)


def _hint(value: str | None) -> tuple[str, str] | None:
    """'<caa_release_mbid> <caa_id>' -> validated pair; anything odd is ignored."""
    if not value:
        return None
    release, _, image = value.partition(" ")
    release = release.lower()
    return (release, image) if MBID.match(release) and CAA_ID.match(image) else None


class SqlArtworkRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _names(self, c: Connection, entity_type: str, mbid: str, kind: str) -> ArtworkNames:
        """Names for an MBID-keyed entity from stored listens only, most frequent first."""
        sql = NAMES_SQL[entity_type]
        if entity_type == "album":
            cond, xcond = _ALBUM_CONDS[kind]
            sql = sql.format(cond=cond, xcond=xcond)
        parts: dict[str, list[str]] = {}
        rows = c.execute(text(sql), {"m": mbid}).all()
        for part, name, _ in sorted(rows, key=lambda r: (-r[2], r[1])):
            if name and name.strip():
                parts.setdefault(part, []).append(name)

        def first(key: str) -> str | None:
            return parts[key][0] if parts.get(key) else None

        if entity_type == "artist":
            titles = parts.get("title", [])
            return ArtworkNames(first("title"), (), tuple(titles[1:3]))
        # individual credits first (better search terms), the submitted credit string after
        artists = dict.fromkeys(parts.get("artist", [])[:MAX_NAMES] + parts.get("credit", [])[:2])
        return ArtworkNames(first("title"), tuple(artists), (), first("release"))

    def due(self, now: datetime, limit: int) -> list[ArtworkWork]:
        params = {"now": now, "limit": limit, "casefold": CASEFOLD, "whitespace": WHITESPACE}
        works: list[ArtworkWork] = []
        with self.engine.connect() as c:
            rows = c.execute(text(DUE_SQL), params).mappings().all()
            for r in rows:
                entity_type = cast(ArtworkEntityType, r["entity_type"])
                kind: Literal["release-group", "release"] = (
                    "release" if r["kind"] == "release" else "release-group"
                )
                if r["mbid"] is None:
                    names = _name_only(entity_type, r["an"], r["tn"], r["rn"])
                else:
                    names = self._names(c, entity_type, r["mbid"], kind)
                works.append(
                    ArtworkWork(
                        entity_type,
                        r["mbid"],
                        r["release_mbid"],
                        int(r["listens"]),
                        kind,
                        _hint(r["caa_hint"]),
                        r["name_key"],
                        names,
                    )
                )
        return works

    @staticmethod
    def _where(work: ArtworkWork) -> tuple[str, str, dict[str, str]]:
        """(table, key predicate, params): MBID works live in `artwork`, name-only in
        `artwork_name`. Both keyed by the stable ranking key."""
        if work.mbid is None:
            return (
                "artwork_name",
                "entity_type=:t AND name_key=:m",
                {"t": work.entity_type, "m": work.ref},
            )
        return (
            "artwork",
            "entity_type=:t AND entity_mbid=CAST(:m AS uuid)",
            {"t": work.entity_type, "m": work.ref},
        )

    def attempts(self, work: ArtworkWork) -> int:
        table, where, key = self._where(work)
        with self.engine.connect() as c:
            value = c.execute(text(f"SELECT attempts FROM {table} WHERE {where}"), key).scalar()
        return int(value or 0)

    def save(
        self, work: ArtworkWork, outcome: ArtworkOutcome, now: datetime, next_retry_at: datetime
    ) -> bool:
        table, where, key = self._where(work)
        by_name = work.mbid is None
        resolved = outcome.status == "resolved"
        with self.engine.begin() as c:
            previous = c.execute(
                text(f"SELECT status, image_url FROM {table} WHERE {where} FOR UPDATE"), key
            ).one_or_none()
            visible_before = previous[1] if previous and previous[0] == "resolved" else None
            if outcome.status == "transient":
                sql = UPSERT_NAME_TRANSIENT if by_name else UPSERT_TRANSIENT
                c.execute(text(sql), {**key, "now": now, "retry": next_retry_at})
                return False
            c.execute(
                text(UPSERT_NAME_FINAL if by_name else UPSERT_FINAL),
                {
                    **key,
                    "source": outcome.source if resolved else "none",
                    "url": outcome.image_url if resolved else None,
                    "status": outcome.status,
                    "now": now,
                    "retry": next_retry_at,
                    "kind": (outcome.match_kind or "id")
                    if resolved
                    else ("none" if by_name else "id"),
                    "ref": outcome.provider_ref if resolved else None,
                },
            )
            return visible_before != (outcome.image_url if resolved else None)

    def bump_generation(self) -> None:
        # Invalidates cached statistics so new image_urls appear. last_updated is untouched:
        # it reports listen ingestion freshness, which artwork does not change.
        with self.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO data_generation (id, enrichment_generation) VALUES (1, 1) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "enrichment_generation = data_generation.enrichment_generation + 1"
                )
            )

    def reset_retries(self) -> int:
        """Make every miss/transient row due now (resolved rows keep their TTL)."""
        with self.engine.begin() as c:
            total = 0
            for table in ("artwork", "artwork_name"):  # closed set
                total += c.execute(
                    text(
                        f"UPDATE {table} SET next_retry_at = now() "
                        "WHERE status IN ('miss','transient')"
                    )
                ).rowcount
            return total

    def enrichment_has_due_work(self, now: datetime) -> bool:
        """Mirror of the enrichment queue's ready() predicate."""
        with self.engine.connect() as c:
            return bool(
                c.execute(
                    text("""
                SELECT EXISTS (SELECT 1 FROM enrichment_queue
                WHERE status <> 'in_progress'
                  AND (status <> 'failed' OR next_retry_at IS NOT NULL)
                  AND (next_retry_at IS NULL OR next_retry_at <= :now))
            """),
                    {"now": now},
                ).scalar()
            )
