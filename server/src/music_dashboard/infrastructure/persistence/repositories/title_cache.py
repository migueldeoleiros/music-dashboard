"""Writer-side release / release-group title cache. Never writes listens (candidates read only).

`release_title_cache` is the positive projection read by the API (entity_type, entity_mbid,
title). `release_title_lookup` is private retry/negative state
(status reflects the LATEST attempt, so a transient after a resolve still counts toward the
source cooldown; the published title stays in the positive cache). Publication of a visibly changed
title and the enrichment_generation bump share ONE short transaction (no network inside).
ingestion_generation and last_updated are never touched.
Callers hold ENRICHMENT_LOCK (session advisory lock) across the run."""

from datetime import datetime

from sqlalchemy import Engine, text

from ..statistics_queries import WHITESPACE

# "Blank" matches the ranking queries: NFKC then trim of the shared Unicode WHITESPACE set.
# Candidates: every release_mbid AND release_group_mbid whose listens carry NO stored non-blank
# release_name (stored titles, and conflicts among them, win), with no lookup row or a due retry.
# Exact releases are candidates even when the listen also has a release group.
CANDIDATES_SQL = """
WITH ents AS (
  SELECT 'release_group'::text AS entity_type, release_group_mbid AS mbid, count(*) AS n
  FROM listens WHERE release_group_mbid IS NOT NULL
  GROUP BY release_group_mbid
  HAVING count(*) FILTER (
    WHERE btrim(normalize(coalesce(release_name, ''), NFKC), :whitespace) <> '') = 0
  UNION ALL
  SELECT 'release', release_mbid, count(*) FROM listens
  WHERE release_mbid IS NOT NULL
  GROUP BY release_mbid
  HAVING count(*) FILTER (
    WHERE btrim(normalize(coalesce(release_name, ''), NFKC), :whitespace) <> '') = 0
)
SELECT e.entity_type, e.mbid::text AS mbid, coalesce(l.attempts, 0) AS attempts
FROM ents e
LEFT JOIN release_title_lookup l ON l.entity_type = e.entity_type AND l.entity_mbid = e.mbid
WHERE l.entity_mbid IS NULL OR (l.next_retry_at IS NOT NULL AND l.next_retry_at <= :now)
ORDER BY e.n DESC, e.entity_type, e.mbid
LIMIT :limit
"""

BUMP_SQL = """
INSERT INTO data_generation (id, enrichment_generation) VALUES (1, 1)
ON CONFLICT (id) DO UPDATE SET enrichment_generation = data_generation.enrichment_generation + 1
"""

UPSERT_LOOKUP = """
INSERT INTO release_title_lookup (entity_type, entity_mbid, status, release_group_mbid,
  fetched_at, next_retry_at, attempts)
VALUES (:t, CAST(:m AS uuid), :s, CAST(:rg AS uuid), :now, :retry, :a)
ON CONFLICT (entity_type, entity_mbid) DO UPDATE SET
  status = EXCLUDED.status,
  release_group_mbid = coalesce(EXCLUDED.release_group_mbid,
                                release_title_lookup.release_group_mbid),
  fetched_at = EXCLUDED.fetched_at, next_retry_at = EXCLUDED.next_retry_at,
  attempts = EXCLUDED.attempts
"""


class SqlTitleCacheRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def candidates(self, now: datetime, limit: int) -> list[tuple[str, str, int]]:
        with self.engine.connect() as c:
            rows = c.execute(
                text(CANDIDATES_SQL), {"now": now, "limit": limit, "whitespace": WHITESPACE}
            ).all()
        return [(r.entity_type, r.mbid, r.attempts) for r in rows]

    def source_retry_at(self) -> datetime | None:
        """Latest transient deadline: a source-wide cooldown that persists across runs."""
        with self.engine.connect() as c:
            return c.execute(
                text(
                    "SELECT max(next_retry_at) FROM release_title_lookup WHERE status = 'transient'"
                )
            ).scalar()

    def save_resolved(
        self,
        entity_type: str,
        mbid: str,
        title: str,
        release_group_mbid: str | None,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool:
        """Upsert verified title + lookup state; bump enrichment_generation iff title changed."""
        p = {"t": entity_type, "m": mbid, "title": title, "rg": release_group_mbid}
        with self.engine.begin() as c:
            c.execute(
                text(UPSERT_LOOKUP),
                {**p, "s": "resolved", "now": now, "retry": next_retry_at, "a": 0},
            )
            inserted = c.execute(
                text(
                    "INSERT INTO release_title_cache (entity_type, entity_mbid, title)"
                    " VALUES (:t, CAST(:m AS uuid), :title)"
                    " ON CONFLICT (entity_type, entity_mbid) DO NOTHING RETURNING 1"
                ),
                p,
            ).first()
            changed = inserted is not None
            if not changed:
                # Row lock serialises concurrent writers: each compares the latest committed value.
                old = c.execute(
                    text(
                        "SELECT title FROM release_title_cache WHERE entity_type = :t"
                        " AND entity_mbid = CAST(:m AS uuid) FOR UPDATE"
                    ),
                    p,
                ).scalar()
                changed = old != title
                if changed:
                    c.execute(
                        text(
                            "UPDATE release_title_cache SET title = :title"
                            " WHERE entity_type = :t AND entity_mbid = CAST(:m AS uuid)"
                        ),
                        p,
                    )
            if changed:
                c.execute(text(BUMP_SQL))
            return changed

    def save_miss(
        self,
        entity_type: str,
        mbid: str,
        status: str,
        next_retry_at: datetime,
        now: datetime,
        attempts: int,
    ) -> None:
        """Negative/transient state only: retry/attempts always persist; the positive projection
        is untouched (an existing title is kept) and the generation is never bumped."""
        if status not in ("not_found", "transient"):
            raise ValueError("miss status must be not_found or transient")
        with self.engine.begin() as c:
            c.execute(
                text(UPSERT_LOOKUP),
                {
                    "t": entity_type,
                    "m": mbid,
                    "s": status,
                    "rg": None,
                    "now": now,
                    "retry": next_retry_at,
                    "a": attempts,
                },
            )
