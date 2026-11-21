"""Artwork persistence (writer role). The API role only reads image_url via statistics joins."""

from datetime import datetime
from typing import cast

from sqlalchemy import Engine, text

from music_dashboard.domain.artwork.entities import ArtworkEntityType, ArtworkOutcome, ArtworkWork
from music_dashboard.infrastructure.artwork.urls import CAA_ID, MBID

# Session-level advisory lock, like ENRICHMENT_LOCK.
ARTWORK_LOCK = 730005

# Most-listened release within the release group / for the recording; ties: smallest mbid.
DUE_SQL = """
WITH ents AS (
  SELECT 'album'::text AS entity_type, release_group_mbid AS mbid, count(*) AS listens,
         'release-group'::text AS kind
  FROM listens WHERE release_group_mbid IS NOT NULL GROUP BY release_group_mbid
  UNION ALL
  -- Album ranking key is coalesce(release_group, release): release-only albums need artwork too.
  SELECT 'album', release_mbid, count(*), 'release' FROM listens
  WHERE release_group_mbid IS NULL AND release_mbid IS NOT NULL GROUP BY release_mbid
  UNION ALL
  SELECT 'track', recording_mbid, count(*), NULL FROM listens
  WHERE recording_mbid IS NOT NULL GROUP BY recording_mbid
  UNION ALL
  SELECT 'artist', artist_mbid, count(DISTINCT listen_id), NULL FROM listen_artists
  WHERE artist_mbid IS NOT NULL GROUP BY artist_mbid
), due AS (
  SELECT e.* FROM ents e
  LEFT JOIN artwork a ON a.entity_type = e.entity_type AND a.entity_mbid = e.mbid
  WHERE a.entity_mbid IS NULL OR a.next_retry_at IS NULL OR a.next_retry_at <= :now
  ORDER BY e.listens DESC, e.entity_type, e.mbid
  LIMIT :limit
)
SELECT d.entity_type, d.mbid::text AS mbid, d.listens, d.kind,
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
       WHEN d.entity_type = 'track' THEN x.recording_mbid = d.mbid
       WHEN d.entity_type = 'album' AND d.kind = 'release' THEN
         x.release_mbid = d.mbid AND x.release_group_mbid IS NULL
       WHEN d.entity_type = 'album' THEN x.release_group_mbid = d.mbid
       ELSE false END
   GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1) AS caa_hint
FROM due d ORDER BY d.listens DESC, d.entity_type, d.mbid
"""

UPSERT_FINAL = """
INSERT INTO artwork (entity_type, entity_mbid, source, image_url, status,
                     fetched_at, next_retry_at, attempts)
VALUES (:t, CAST(:m AS uuid), :source, :url, :status, :now, :retry, 0)
ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET
  source = EXCLUDED.source, image_url = EXCLUDED.image_url, status = EXCLUDED.status,
  fetched_at = EXCLUDED.fetched_at, next_retry_at = EXCLUDED.next_retry_at, attempts = 0
"""

# A refresh failure never discards an already-resolved image: only the retry schedule moves.
UPSERT_TRANSIENT = """
INSERT INTO artwork (entity_type, entity_mbid, source, image_url, status,
                     fetched_at, next_retry_at, attempts)
VALUES (:t, CAST(:m AS uuid), 'none', NULL, 'transient', :now, :retry, 1)
ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET
  next_retry_at = EXCLUDED.next_retry_at, attempts = artwork.attempts + 1
"""


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

    def due(self, now: datetime, limit: int) -> list[ArtworkWork]:
        with self.engine.connect() as c:
            rows = c.execute(text(DUE_SQL), {"now": now, "limit": limit}).mappings().all()
        return [
            ArtworkWork(
                cast(ArtworkEntityType, r["entity_type"]),
                r["mbid"],
                r["release_mbid"],
                int(r["listens"]),
                "release" if r["kind"] == "release" else "release-group",
                _hint(r["caa_hint"]),
            )
            for r in rows
        ]

    def attempts(self, work: ArtworkWork) -> int:
        with self.engine.connect() as c:
            value = c.execute(
                text(
                    "SELECT attempts FROM artwork "
                    "WHERE entity_type=:t AND entity_mbid=CAST(:m AS uuid)"
                ),
                {"t": work.entity_type, "m": work.mbid},
            ).scalar()
        return int(value or 0)

    def save(
        self, work: ArtworkWork, outcome: ArtworkOutcome, now: datetime, next_retry_at: datetime
    ) -> bool:
        key = {"t": work.entity_type, "m": work.mbid}
        with self.engine.begin() as c:
            previous = c.execute(
                text(
                    "SELECT status, image_url FROM artwork "
                    "WHERE entity_type=:t AND entity_mbid=CAST(:m AS uuid) FOR UPDATE"
                ),
                key,
            ).one_or_none()
            visible_before = previous[1] if previous and previous[0] == "resolved" else None
            if outcome.status == "transient":
                c.execute(text(UPSERT_TRANSIENT), {**key, "now": now, "retry": next_retry_at})
                return False
            c.execute(
                text(UPSERT_FINAL),
                {
                    **key,
                    "source": outcome.source if outcome.status == "resolved" else "none",
                    "url": outcome.image_url if outcome.status == "resolved" else None,
                    "status": outcome.status,
                    "now": now,
                    "retry": next_retry_at,
                },
            )
            visible_after = outcome.image_url if outcome.status == "resolved" else None
            return visible_before != visible_after

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
            return c.execute(
                text(
                    "UPDATE artwork SET next_retry_at = now() WHERE status IN ('miss','transient')"
                )
            ).rowcount

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
