"""Artist-scoped SQL. Raw event ordering remains private inside the adapter."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text

from music_dashboard.infrastructure.artwork.urls import validate_image_url
from music_dashboard.infrastructure.persistence.statistics_queries import (
    ALBUM_RANKING,
    CASEFOLD,
    COMMON,
    FOLD,
    TRACK_RANKING,
    WHITESPACE,
)

# Reuse frozen identity/title policies without changing the frozen aggregate queries.
# MBID selection uses the existing artist index. Name selection normalizes distinct
# credit names, not hydrated listening history. EXISTS avoids duplicated credits.
MATCH = f"""
WITH candidate_names AS (
 SELECT DISTINCT artist_name AS name FROM listen_artists WHERE CAST(:mbid AS text) IS NULL
 UNION SELECT DISTINCT artist_name FROM listens l WHERE CAST(:mbid AS text) IS NULL
 AND NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id=l.id)
), folded_names AS MATERIALIZED (
 SELECT name, {FOLD} AS folded FROM candidate_names
), matched AS MATERIALIZED (
 SELECT DISTINCT a.listen_id AS id FROM listen_artists a
 WHERE (CAST(:mbid AS text) IS NOT NULL AND a.artist_mbid=CAST(:mbid AS uuid))
 OR (CAST(:mbid AS text) IS NULL AND a.artist_mbid IS NULL AND EXISTS (
   SELECT 1 FROM folded_names n WHERE n.name=a.artist_name AND n.folded=:name))
 UNION ALL
 SELECT l.id FROM listens l WHERE CAST(:mbid AS text) IS NULL
 AND NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id=l.id)
 AND EXISTS (SELECT 1 FROM folded_names n WHERE n.name=l.artist_name AND n.folded=:name)
), selected AS MATERIALIZED (
 SELECT l.*, (l.listened_at AT TIME ZONE :tz)::date AS day
 FROM matched m JOIN listens l ON l.id=m.id
)
"""
# COMMON's later CTEs retain canonical album resolution over ALL history and the
# established missing-metadata rules, while selected is restricted to this artist.
SCOPED = MATCH + ", names AS (" + COMMON.split("), names AS (", 1)[1]


def params(key: str, tz: str = "UTC") -> dict[str, Any]:
    return {
        "key": key,
        "mbid": None if key.startswith("name:") else key,
        "name": key[5:] if key.startswith("name:") else None,
        "casefold": CASEFOLD,
        "whitespace": WHITESPACE,
        "tz": tz,
        "country_codes": [],
    }


def identity(c: Connection, p: dict[str, Any]) -> dict[str, Any] | None:
    row = (
        c.execute(
            text(
                SCOPED
                + """
 SELECT min(label) AS label, count(*) AS total FROM unique_credits
 WHERE key=:key AND state<>'missing' HAVING count(*)>0
 """
            ),
            p,
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def artwork(c: Connection, key: str, kind: str) -> str | None:
    if key.startswith("name:"):
        sql = """SELECT image_url FROM artwork_name WHERE entity_type=:kind
                 AND name_key=:key AND status='resolved'"""
    else:
        sql = """SELECT image_url FROM artwork WHERE entity_type=:kind
                 AND entity_mbid=CAST(:key AS uuid) AND status='resolved'"""
    return validate_image_url(c.execute(text(sql), {"key": key, "kind": kind}).scalar())


def rankings(
    c: Connection,
    p: dict[str, Any],
    dimension: str,
    limit: int,
    after: tuple[int, str] | None,
) -> list[dict[str, Any]]:
    # Identifiers come only from the closed dimension map, never user SQL.
    extra, source = {
        "albums": (ALBUM_RANKING, "album_ranking_values"),
        "tracks": (TRACK_RANKING, "track_ranking_values"),
    }[dimension]
    rows = c.execute(
        text(
            SCOPED
            + extra
            + f"""
 , totals AS (
   SELECT key, min(label) AS label, count(*)::bigint AS listens
   FROM {source} GROUP BY key
 )
 SELECT key, label, listens FROM totals
 WHERE CAST(:after_count AS bigint) IS NULL OR listens<CAST(:after_count AS bigint)
 OR (listens=CAST(:after_count AS bigint) AND key>:after_key)
 ORDER BY listens DESC,key LIMIT :limit
 """
        ),
        {
            **p,
            "after_count": after[0] if after else None,
            "after_key": after[1] if after else "",
            "limit": limit + 1,
        },
    ).mappings()
    return [dict(row) for row in rows]


def recent(
    c: Connection,
    p: dict[str, Any],
    limit: int,
    after: tuple[str, int] | None,
) -> list[dict[str, Any]]:
    # Page BEFORE projecting credits; each repeated listen stays a separate row.
    return [
        dict(row)
        for row in c.execute(
            text(
                MATCH
                + f"""
 , page AS MATERIALIZED (
 SELECT * FROM selected WHERE CAST(:after_at AS text) IS NULL
 OR (listened_at,id)<(CAST(CAST(:after_at AS text) AS timestamptz),CAST(:after_id AS bigint))
 ORDER BY listened_at DESC,id DESC LIMIT :limit
 ), credit_names AS (
 SELECT DISTINCT a.artist_name AS name FROM listen_artists a JOIN page l ON l.id=a.listen_id
 UNION SELECT p.artist_name FROM page p
 ), credit_folded AS (
 SELECT name, {FOLD} AS folded FROM credit_names
 )
 SELECT l.id, l.listened_at,
 coalesce(nullif(btrim(l.track_name,:whitespace),''),'[Missing title]') AS title,
 nullif(btrim(l.release_name,:whitespace),'') AS album,
 coalesce((SELECT jsonb_agg(jsonb_build_object(
   'key', CASE WHEN coalesce(n.folded,'')='' THEN NULL
               ELSE coalesce(a.artist_mbid::text,'name:'||n.folded) END,
   'label', coalesce(nullif(btrim(a.artist_name,:whitespace),''),'[Missing artist]'))
   ORDER BY a.position)
 FROM listen_artists a LEFT JOIN credit_folded n ON n.name=a.artist_name
 WHERE a.listen_id=l.id), jsonb_build_array(jsonb_build_object(
   'key', CASE WHEN coalesce(n.folded,'')='' THEN NULL ELSE 'name:'||n.folded END,
   'label',coalesce(nullif(btrim(l.artist_name,:whitespace),''),'[Missing artist]')))) AS artists
 FROM page l LEFT JOIN credit_folded n ON n.name=l.artist_name
 ORDER BY l.listened_at DESC,l.id DESC
 """
            ),
            {
                **p,
                "after_at": after[0] if after else None,
                "after_id": after[1] if after else 0,
                "limit": limit + 1,
            },
        ).mappings()
    ]


def credit_sets(c: Connection, dimension: str, mbid: str) -> list[list[dict[str, Any]]]:
    """Distinct ordered credit lists across listens of one album/track id (max 2 returned)."""
    where = (
        "l.recording_mbid=CAST(:mbid AS uuid)"
        if dimension == "tracks"
        else "(l.release_group_mbid=CAST(:mbid AS uuid) OR l.release_mbid=CAST(:mbid AS uuid))"
    )
    rows = c.execute(
        text(
            f"""
 WITH sel AS MATERIALIZED (SELECT l.id, l.artist_name FROM listens l WHERE {{where}}),
 raw AS (
  SELECT s.id, a.position, a.artist_name AS name, a.artist_mbid::text AS mbid
  FROM sel s JOIN listen_artists a ON a.listen_id=s.id
  UNION ALL SELECT s.id, 0, s.artist_name, NULL FROM sel s
  WHERE NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id=s.id)
 ), folded AS (
  SELECT name, {FOLD} AS folded FROM (SELECT DISTINCT name FROM raw) d
 ), per AS (
  SELECT r.id, jsonb_agg(DISTINCT jsonb_build_object(
    'key', CASE WHEN coalesce(f.folded,'')='' THEN NULL
                ELSE coalesce(r.mbid,'name:'||f.folded) END,
    'label', coalesce(nullif(btrim(r.name,:whitespace),''),'[Missing artist]'))) AS credits
  FROM raw r LEFT JOIN folded f ON f.name=r.name GROUP BY r.id
 ) SELECT DISTINCT credits FROM per LIMIT 2
 """.replace("{where}", where)
        ),
        {"mbid": mbid, "casefold": CASEFOLD, "whitespace": WHITESPACE},
    ).scalars()
    out = []
    for credits in rows:
        # Unresolved (null key) credits make the whole set unreliable: never guess.
        if any(x["key"] is None for x in credits):
            return []
        out.append(sorted(credits, key=lambda x: (x["label"], x["key"])))
    return out
