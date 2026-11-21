"""Read-only PostgreSQL chart adapter. Only aggregate rows leave the database.

All local boundaries and buckets are PostgreSQL date/timestamp operations. No
Python timezone or timedelta arithmetic. Identity normalization uses PostgreSQL
NFKC plus a parameterized Unicode casefold map (PG17 lower() is not casefold).
The map operates on distinct names, never on hydrated listen history.
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from threading import Lock
from time import monotonic
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from music_dashboard.domain.statistics.models import Coverage, StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    CACHE_MAX_BYTES,
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    COUNTRY_ARTISTS_FAMILY,
    COUNTRY_CODES,
    DIMENSIONS,
    MAX_BUCKETS,
    MAX_EVOLUTION_VALUES,
    SUPPORTED_FAMILIES,
    InvalidStatisticsRequest,
    StatisticsUnavailable,
    validate_country_code,
    validate_filters,
)
from music_dashboard.infrastructure.artwork.urls import validate_image_url
from music_dashboard.infrastructure.persistence.timezones import load_timezones

# Preserve full Unicode casefold (ß, sigma, ligatures, etc.), not locale lower().
CASEFOLD = json.dumps(
    {chr(i): chr(i).casefold() for i in range(sys.maxunicode + 1) if chr(i).casefold() != chr(i)},
    ensure_ascii=True,
)

# Shared with the artwork repository so name-only artwork keys match ranking keys exactly.
WHITESPACE = "".join(chr(i) for i in range(0x3100) if chr(i).isspace())

COMMON = """
WITH bounds AS (
 -- Indexed prefilter only: ambiguous midnight can map to its SECOND
 -- occurrence. The local-date predicate selects the exact half-open range,
 -- including BOTH occurrences and respecting skipped dates. IANA jumps <=24h.
 SELECT (CAST(:from_date AS date)-1)::timestamp AT TIME ZONE :tz AS lo,
        (CAST(:to_date AS date)+2)::timestamp AT TIME ZONE :tz AS hi
), selected AS MATERIALIZED (
 SELECT l.*, (l.listened_at AT TIME ZONE :tz)::date AS day
 FROM listens l, bounds b WHERE l.listened_at >= b.lo AND l.listened_at < b.hi
 AND (l.listened_at AT TIME ZONE :tz)::date
     BETWEEN CAST(:from_date AS date) AND CAST(:to_date AS date)
), names AS (
 SELECT artist_name AS name FROM selected UNION SELECT track_name FROM selected
 UNION SELECT release_name FROM selected
 UNION SELECT a.artist_name FROM listen_artists a JOIN selected l ON l.id=a.listen_id
), normalized AS MATERIALIZED (
 SELECT name, coalesce((SELECT string_agg(
   coalesce(CAST(:casefold AS jsonb)->>ch, ch), '' ORDER BY ord)
   FROM regexp_split_to_table(btrim(normalize(name, NFKC), :whitespace), '')
        WITH ORDINALITY AS chars(ch, ord)), '') AS folded
 FROM names WHERE name IS NOT NULL
), credits AS (
 SELECT DISTINCT l.id, l.day, a.artist_mbid::text AS mbid,
        coalesce(nullif(btrim(a.artist_name, :whitespace), ''), '[Missing artist]') AS label,
        coalesce(a.artist_mbid::text, 'name:' || n.folded) AS key,
        CASE WHEN coalesce(n.folded,'')='' THEN 'missing'
             WHEN a.artist_mbid IS NULL THEN 'unmapped' ELSE 'resolved' END AS state
 FROM selected l JOIN listen_artists a ON a.listen_id=l.id
 LEFT JOIN normalized n ON n.name=a.artist_name
 UNION ALL
 SELECT l.id, l.day, NULL,
        coalesce(nullif(btrim(l.artist_name, :whitespace),''),'[Missing artist]'),
        'name:' || coalesce(n.folded,''),
        CASE WHEN coalesce(n.folded,'')='' THEN 'missing' ELSE 'unmapped' END
 FROM selected l LEFT JOIN normalized n ON n.name=l.artist_name
 WHERE NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id=l.id)
), unique_credits AS (
 SELECT id, day, key, mbid, min(label) AS label, min(state) AS state
 FROM credits GROUP BY id, day, key, mbid
), artist_values AS (
 SELECT a.id, a.day, a.key, a.label, a.mbid, a.state,
        1.0 / count(*) OVER (PARTITION BY a.id) AS weight
 FROM unique_credits a WHERE a.state<>'missing'
), candidates AS (
 SELECT l.id, l.day, 1 AS priority, 'recording' AS entity_type, l.recording_mbid AS entity_mbid
 FROM selected l WHERE l.recording_mbid IS NOT NULL
 UNION ALL
 SELECT l.id, l.day, 2, 'release_group', l.release_group_mbid FROM selected l
 WHERE l.release_group_mbid IS NOT NULL
 UNION ALL
 SELECT id, day, 3, 'artist', CAST(mbid AS uuid) FROM unique_credits WHERE mbid IS NOT NULL
), genre_votes AS (
 SELECT c.*, e.genre_mbid, g.name AS label, e.votes,
   sum(greatest(e.votes,0)) OVER (PARTITION BY c.id,c.priority,c.entity_mbid) AS positive,
   count(*) OVER (PARTITION BY c.id,c.priority,c.entity_mbid) AS n,
   min(c.priority) OVER (PARTITION BY c.id) AS best,
   row_number() OVER (PARTITION BY c.id,c.priority,c.entity_mbid
                      ORDER BY e.genre_mbid) AS entity_row
 FROM candidates c JOIN entity_genres e USING (entity_type,entity_mbid)
 JOIN genres g ON g.genre_mbid=e.genre_mbid
), winning_votes AS (
 SELECT *, sum(CASE WHEN entity_row=1 THEN 1 ELSE 0 END)
              OVER (PARTITION BY id) AS entities
 FROM genre_votes WHERE priority=best
), genre_values AS (
 SELECT v.id, v.day, v.genre_mbid::text AS key, v.label,
        v.genre_mbid::text AS mbid, 'resolved' AS state,
        (CASE WHEN v.positive>0 THEN greatest(v.votes,0)::numeric/v.positive
              ELSE 1.0/v.n END) / v.entities AS weight
 FROM winning_votes v WHERE v.positive=0 OR v.votes>0
), country_credits AS (
 SELECT DISTINCT a.id, a.mbid, c.country_code AS code
 FROM unique_credits a JOIN artist_countries c ON c.artist_mbid=CAST(a.mbid AS uuid)
 WHERE c.country_code = ANY(:country_codes)
), country_values AS (
 SELECT id, code, 1.0 / count(*) OVER (PARTITION BY id) AS weight FROM country_credits
), album_history AS MATERIALIZED (
 -- Stable identity across date ranges: canonical albums come from ALL history, not the
 -- selected window. Read-only; raw listens are never rewritten. Only evaluated for albums.
 SELECT DISTINCT artist_name, release_name,
        coalesce(release_group_mbid,release_mbid)::text AS kid
 FROM listens WHERE coalesce(release_group_mbid,release_mbid) IS NOT NULL
 AND artist_name IS NOT NULL AND release_name IS NOT NULL
 -- Skip the history scan entirely when the window has no unmapped named album.
 AND EXISTS (SELECT 1 FROM selected s WHERE s.release_group_mbid IS NULL
             AND s.release_mbid IS NULL AND s.release_name IS NOT NULL)
), album_history_names AS (
 SELECT artist_name AS name FROM album_history UNION SELECT release_name FROM album_history
), album_history_normalized AS MATERIALIZED (
 SELECT name, coalesce((SELECT string_agg(
   coalesce(CAST(:casefold AS jsonb)->>ch, ch), '' ORDER BY ord)
   FROM regexp_split_to_table(btrim(normalize(name, NFKC), :whitespace), '')
        WITH ORDINALITY AS chars(ch, ord)), '') AS folded
 FROM album_history_names
), canonical_albums AS (
 -- Exact normalized (artist, album) -> exactly ONE known id. Two or more distinct ids
 -- (e.g. deluxe vs original, release vs release group) stay ambiguous: no entry.
 SELECT a.folded AS artist, n.folded AS album, min(h.kid) AS kid
 FROM album_history h
 JOIN album_history_normalized a ON a.name=h.artist_name
 JOIN album_history_normalized n ON n.name=h.release_name
 WHERE a.folded<>'' AND n.folded<>''
 GROUP BY a.folded, n.folded HAVING count(DISTINCT h.kid)=1
), album_values AS (
 SELECT l.id, l.day,
   coalesce(l.release_group_mbid::text,l.release_mbid::text,c.kid,
     'name:' || jsonb_build_array(coalesce(a.folded,''),coalesce(n.folded,''))::text) AS key,
   coalesce(nullif(btrim(l.release_name, :whitespace),''),'[Missing album]') AS label,
   coalesce(l.release_group_mbid::text,l.release_mbid::text,c.kid) AS mbid,
   CASE WHEN coalesce(n.folded,'')='' THEN 'missing'
        WHEN coalesce(l.release_group_mbid::text,l.release_mbid::text,c.kid) IS NULL
          THEN 'unmapped'
        ELSE 'resolved' END AS state, 1.0 AS weight
 FROM selected l LEFT JOIN normalized n ON n.name=l.release_name
 LEFT JOIN normalized a ON a.name=l.artist_name
 LEFT JOIN canonical_albums c ON l.release_group_mbid IS NULL AND l.release_mbid IS NULL
  AND c.artist=a.folded AND c.album=n.folded
), track_values AS (
 SELECT l.id, l.day,
   coalesce(l.recording_mbid::text,
     'name:' || jsonb_build_array(coalesce(a.folded,''),coalesce(n.folded,''))::text) AS key,
   coalesce(nullif(btrim(l.track_name, :whitespace),''),'[Missing title]') AS label,
   l.recording_mbid::text AS mbid,
   CASE WHEN coalesce(n.folded,'')='' THEN 'missing'
        WHEN l.recording_mbid IS NULL THEN 'unmapped' ELSE 'resolved' END AS state,
   1.0 AS weight
 FROM selected l LEFT JOIN normalized n ON n.name=l.track_name
 LEFT JOIN normalized a ON a.name=l.artist_name
)
"""

COVERAGE = """
SELECT count(*) AS total,
 (SELECT count(DISTINCT id) FROM artist_values) AS artists,
 (SELECT count(DISTINCT id) FROM genre_values) AS genres,
 (SELECT count(DISTINCT id) FROM country_values) AS countries
FROM selected
"""
# Same country_credits (1/N over unique country-resolved credits) as country_values, so each
# country's artist listens sum to its /api/countries total for identical filters.
COUNTRY_ARTISTS = """
, credit_weights AS (
 SELECT id, mbid, code, 1.0 / count(*) OVER (PARTITION BY id) AS weight FROM country_credits
)
SELECT w.mbid AS key, min(u.label) AS label, w.mbid AS mbid, max(art.image_url) AS image_url,
       CASE WHEN bool_or(u.state='missing') THEN 'missing'
            WHEN bool_or(u.state='resolved') THEN 'resolved' ELSE 'unmapped' END AS state,
       sum(w.weight)::float8 AS listens
FROM credit_weights w JOIN unique_credits u ON u.id=w.id AND u.mbid=w.mbid
LEFT JOIN artwork art ON art.entity_type='artist' AND art.status='resolved'
 AND art.entity_mbid=CAST(w.mbid AS uuid)  -- PK (entity_type, entity_mbid): at most one row
WHERE w.code=:country GROUP BY w.mbid ORDER BY listens DESC, key LIMIT :limit
"""
# Artwork is keyed by exact MBID (albums by release group, tracks by recording, artists by MBID)
# in `artwork`, or by the stable `name:` ranking key in `artwork_name` for entities without an
# MBID. A key is either a UUID or `name:...`, so the two joins can never both match. The join
# only reads image_url: keys, labels, totals and ordering never depend on artwork. Genres have
# no artwork.
ARTWORK_TYPES = {"artists": "artist", "albums": "album", "tracks": "track"}
UUID_KEY = "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
TABLES = {
    "artists": "artist_values",
    "albums": "album_values",
    "tracks": "track_values",
    "genres": "genre_values",
}


# Closed, internal identifier substitution only. Every user value remains bound.
def totals_sql(dimension: str) -> str:
    return f"""
 SELECT key,
        coalesce(min(label) FILTER (WHERE mbid IS NOT NULL), min(label)) AS label,
        min(mbid) AS mbid,
        CASE WHEN bool_or(state='missing') THEN 'missing'
             WHEN bool_or(state='resolved') THEN 'resolved' ELSE 'unmapped' END AS state,
        sum(weight)::float8 AS listens
 FROM {TABLES[dimension]} GROUP BY key
 """


class ResponseCache:
    """Thread-safe TTL/LRU with entry AND serialized byte budgets; copies on access."""

    def __init__(
        self,
        ttl: float = CACHE_TTL_SECONDS,
        max_entries: int = CACHE_MAX_ENTRIES,
        max_bytes: int = CACHE_MAX_BYTES,
    ) -> None:
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self.entries: OrderedDict[tuple[Any, ...], tuple[float, int, dict[str, Any]]] = (
            OrderedDict()
        )
        self.bytes = 0
        self.lock = Lock()

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return None
            if monotonic() >= entry[0]:
                self.bytes -= self.entries.pop(key)[1]
                return None
            self.entries.move_to_end(key)
            return deepcopy(entry[2])

    def put(self, key: tuple[Any, ...], response: dict[str, Any]) -> None:
        size = len(json.dumps(response, default=str).encode())
        if size > self.max_bytes:
            return
        with self.lock:
            if key in self.entries:
                self.bytes -= self.entries.pop(key)[1]
            self.entries[key] = (monotonic() + self.ttl, size, deepcopy(response))
            self.bytes += size
            while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
                self.bytes -= self.entries.popitem(last=False)[1][1]


class SQLStatisticsQueries:
    def __init__(self, engine: Engine, cache: ResponseCache | None = None) -> None:
        self.engine = engine
        self.cache = cache if cache is not None else ResponseCache()

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        try:
            with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
                yield c
        except SQLAlchemyError:
            # Never surface SQL, connection URLs or driver diagnostics publicly.
            raise StatisticsUnavailable("Statistics database unavailable") from None

    @staticmethod
    def _snapshot(c: Connection) -> dict[str, Any]:
        c.execute(text("SET TRANSACTION READ ONLY"))
        c.execute(text("SELECT set_config('statement_timeout', '5000', true)"))
        row = (
            c.execute(
                text("""
          SELECT ingestion_generation + enrichment_generation AS generation,
                 CASE WHEN ingestion_generation > 0 THEN last_updated END AS last_updated,
                 CASE WHEN ingestion_generation > 0 THEN
                   greatest(0, floor(extract(epoch FROM (now()-last_updated))))::bigint END AS lag
          FROM data_generation WHERE id=1
        """)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else {"generation": 0, "last_updated": None, "lag": None}

    @staticmethod
    def _params(filters: StatisticsFilter) -> dict[str, Any]:
        validate_filters(filters.from_date, filters.to_date, filters.limit)
        if filters.country_code is not None:
            validate_country_code(filters.country_code)
        zones = load_timezones()
        if filters.timezone not in zones:
            raise InvalidStatisticsRequest("Unsupported IANA timezone")
        return {
            "from_date": filters.from_date,
            "to_date": filters.to_date,
            "tz": zones[filters.timezone],
            "limit": filters.limit,
            "casefold": CASEFOLD,
            "whitespace": WHITESPACE,
            "country_codes": COUNTRY_CODES,
            "country": filters.country_code,
        }

    def _coverage(self, c: Connection, params: dict[str, Any], generation: int) -> dict[str, Any]:
        key = ("coverage", params["from_date"], params["to_date"], params["tz"], generation)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        counts = c.execute(text(COMMON + COVERAGE), params).mappings().one()
        total = counts["total"]
        result = {
            d: Coverage(
                total, counts[d] if d in ("artists", "genres", "countries") else total
            ).as_dict()
            for d in DIMENSIONS
        }
        self.cache.put(key, result)
        return result

    def query(self, family: str, filters: StatisticsFilter) -> dict[str, Any]:
        if family not in SUPPORTED_FAMILIES:
            raise InvalidStatisticsRequest("Unsupported chart family")
        params = self._params(filters)
        with self._connection() as c:
            snapshot = self._snapshot(c)
            key = (
                family,
                params["from_date"],
                params["to_date"],
                params["tz"],
                filters.limit,
                filters.country_code,
                snapshot["generation"],
            )
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            result: dict[str, Any] = {
                "range": {
                    "from_date": filters.from_date,
                    "to_date": filters.to_date,
                    "tz": params["tz"],
                },
                "coverage": self._coverage(c, params, snapshot["generation"]),
                "generation": snapshot["generation"],
                "last_updated": snapshot["last_updated"],
                "fixture": False,
            }
            if family == "heatmap":
                # Sparse daily cells keep all-time valid; missing dates mean zero.
                result.update(
                    week_starts_on="monday",
                    cells=[
                        dict(r)
                        for r in c.execute(
                            text(
                                COMMON + "SELECT day, count(*) AS listens FROM selected "
                                "GROUP BY day ORDER BY day"
                            ),
                            params,
                        ).mappings()
                    ],
                )
            elif family == COUNTRY_ARTISTS_FAMILY:
                if filters.country_code is None:
                    raise InvalidStatisticsRequest("Unsupported country code")
                included = result["coverage"]["countries"]["included_listens"]
                result["dimension"] = "artists"
                result["items"] = [
                    {
                        "key": r["key"],
                        "label": r["label"],
                        "mbid": r["mbid"],
                        "metadata_state": r["state"],
                        "listens": r["listens"],
                        # Same denominator as /api/countries: shares sum to that country's share.
                        "share": self._share(r["listens"], included),
                        "image_url": validate_image_url(r["image_url"]),
                    }
                    for r in c.execute(text(COMMON + COUNTRY_ARTISTS), params).mappings()
                ]
            elif family == "countries":
                included = result["coverage"]["countries"]["included_listens"]
                result["countries"] = [
                    {
                        "code": r["code"],
                        "listens": r["listens"],
                        "share": self._share(r["listens"], included),
                    }
                    for r in c.execute(
                        text(
                            COMMON
                            + """
                      SELECT code, sum(weight)::float8 AS listens FROM country_values
                      GROUP BY code ORDER BY listens DESC, code
                    """
                        ),
                        params,
                    ).mappings()
                ]
            else:
                kind, dimension = family.split("/")
                result["dimension"] = dimension
                included = result["coverage"][dimension]["included_listens"]
                if kind == "evolution":
                    result.update(self._evolution(c, dimension, params))
                else:
                    # LIMIT N+1 cannot give Other's exact sum, so SQL returns top N + sum(tail).
                    art_type = ARTWORK_TYPES.get(dimension)
                    # PK (entity_type, entity_mbid) makes the join at most one row per item.
                    art_join = (
                        " LEFT JOIN artwork art ON art.entity_type=:art_type AND "
                        "art.status='resolved' AND art.entity_mbid="
                        f"(CASE WHEN r.key ~ {UUID_KEY} THEN CAST(r.key AS uuid) END)"
                        # Name-only keys: the stable `name:` ranking key itself (PK again).
                        " LEFT JOIN artwork_name aname ON aname.entity_type=:art_type AND "
                        "aname.status='resolved' AND aname.name_key="
                        "(CASE WHEN left(r.key,5)='name:' THEN r.key END)"
                        if art_type
                        else ""
                    )
                    rows = (
                        c.execute(
                            text(
                                COMMON
                                + ", totals AS ("
                                + totals_sql(dimension)
                                + """),
                      ranked AS (SELECT *, row_number() OVER (ORDER BY listens DESC,key) AS rank
                                 FROM totals)
                      SELECT r.key,r.label,r.mbid,r.state,r.listens,false AS is_other,
                             """
                                + (
                                    "coalesce(art.image_url, aname.image_url)"
                                    if art_type
                                    else "NULL::text"
                                )
                                + """ AS image_url
                      FROM ranked r"""
                                + art_join
                                + """
                      WHERE r.rank<=:limit
                      UNION ALL
                      SELECT 'other','Other',NULL,'resolved',sum(listens),true,NULL::text
                      FROM ranked
                      WHERE rank>:limit HAVING count(*)>0
                      ORDER BY is_other, listens DESC, key
                    """
                            ),
                            {**params, "art_type": art_type} if art_type else params,
                        )
                        .mappings()
                        .all()
                    )
                    if kind == "rankings":
                        result["items"] = [
                            {
                                "key": r["key"],
                                "label": r["label"],
                                "mbid": r["mbid"],
                                "metadata_state": r["state"],
                                "listens": r["listens"],
                                "share": self._share(r["listens"], included),
                                "image_url": validate_image_url(r["image_url"]),
                            }
                            for r in rows
                            if not r["is_other"]
                        ]
                    else:
                        result["slices"] = [
                            {
                                "key": r["key"],
                                "label": r["label"],
                                "is_other": r["is_other"],
                                "listens": r["listens"],
                                "share": self._share(r["listens"], included),
                            }
                            for r in rows
                        ]
            self.cache.put(key, result)
            return result

    @staticmethod
    def _share(value: float, included: int) -> float:
        return min(1.0, max(0.0, value / included)) if included else 0.0

    @staticmethod
    def _evolution(c: Connection, dimension: str, params: dict[str, Any]) -> dict[str, Any]:
        # Extreme calendar bounds express all-time: trim only those empty edges to
        # history. Ordinary custom ranges retain every requested zero-filled bucket.
        extent = (
            c.execute(
                text(
                    COMMON
                    + """
          , extent AS (
            SELECT CASE WHEN count(*)>0 THEN CASE
                     WHEN CAST(:from_date AS date)=date '0001-01-01'
                     THEN (min(listened_at) AT TIME ZONE :tz)::date
                     ELSE CAST(:from_date AS date) END END AS lo,
                   CASE WHEN count(*)>0 THEN CASE
                     WHEN CAST(:to_date AS date)=date '9999-12-31'
                     THEN (max(listened_at) AT TIME ZONE :tz)::date
                     ELSE CAST(:to_date AS date) END END AS hi
            FROM listens
          )
          SELECT lo, hi, CASE WHEN hi-lo<90 THEN 'day'
                 WHEN hi-lo<730 THEN 'week' ELSE 'month' END AS unit,
                 (SELECT count(DISTINCT key) FROM """
                    + TABLES[dimension]
                    + """
                 ) AS categories FROM extent
        """
                ),
                params,
            )
            .mappings()
            .one()
        )
        unit = extent["unit"]
        if extent["lo"] is None or extent["lo"] > extent["hi"]:
            return {"bucket_unit": "day", "buckets": [], "series": []}
        if extent["hi"] == date.max:
            raise InvalidStatisticsRequest("Exclusive bucket end exceeds the supported calendar")
        p = {**params, "unit": unit, "lo": extent["lo"], "hi": extent["hi"]}
        buckets = [
            dict(r)
            for r in c.execute(
                text("""
          SELECT greatest(d::date, CAST(:from_date AS date)) AS start,
                 least((d+CAST('1 ' || :unit AS interval))::date,
                       CAST(:to_date AS date)+1) AS end,
                 (d+CAST('1 ' || :unit AS interval))::date > CAST(:to_date AS date)+1 AS partial
          FROM generate_series(date_trunc(:unit, CAST(:lo AS timestamp)),
               date_trunc(:unit, CAST(:hi AS timestamp)), CAST('1 ' || :unit AS interval)) d
          ORDER BY d
        """),
                p,
            ).mappings()
        ]
        if len(buckets) > MAX_BUCKETS:
            raise InvalidStatisticsRequest("Range exceeds the supported bucket budget")
        series_count = min(extent["categories"], params["limit"]) + int(
            extent["categories"] > params["limit"]
        )
        if len(buckets) * series_count > MAX_EVOLUTION_VALUES:
            raise InvalidStatisticsRequest("Range and limit exceed the evolution value budget")
        rows = c.execute(
            text(
                COMMON
                + ", totals AS ("
                + totals_sql(dimension)
                + """),
          top AS (SELECT key FROM totals ORDER BY listens DESC,key LIMIT :limit),
          grouped AS (
            SELECT CASE WHEN t.key IS NULL THEN 'other' ELSE v.key END AS key,
              CASE WHEN t.key IS NULL THEN 'Other' ELSE min(v.label) END AS label,
              t.key IS NULL AS is_other, date_trunc(:unit,v.day)::date AS bucket,
              sum(v.weight)::float8 AS listens
            FROM """
                + TABLES[dimension]
                + """ v LEFT JOIN top t ON t.key=v.key
            GROUP BY t.key,v.key,date_trunc(:unit,v.day)
          )
          SELECT key,min(label) AS label,is_other,bucket,sum(listens)::float8 AS listens
          FROM grouped GROUP BY key,is_other,bucket ORDER BY is_other,key,bucket
        """
            ),
            p,
        ).mappings()
        bucket_index = {r["start"]: i for i, r in enumerate(buckets)}
        series: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["key"] not in series:
                series[r["key"]] = {
                    "key": r["key"],
                    "label": r["label"],
                    "is_other": r["is_other"],
                    "values": [0.0] * len(buckets),
                }
            s = series[r["key"]]
            # First partial week/month starts at the requested date rather than before it.
            bucket = max(r["bucket"], params["from_date"])
            s["values"][bucket_index[bucket]] = r["listens"]
        return {"bucket_unit": unit, "buckets": buckets, "series": list(series.values())}

    def health(self) -> dict[str, Any]:
        params = self._params(StatisticsFilter(date.min, date.max, "UTC", 10))
        with self._connection() as c:
            s = self._snapshot(c)
            return {
                "status": "ok" if s["lag"] is not None and s["lag"] <= 86400 else "degraded",
                "mode": "live",
                "last_updated": s["last_updated"],
                "sync_lag_seconds": s["lag"],
                "generation": s["generation"],
                "coverage": self._coverage(c, params, s["generation"]),
            }
