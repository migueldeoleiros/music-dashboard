"""Read-only album-cover fallback for artist rankings (API role; no writes, no upstream calls).

Only runs for the displayed artists that have no resolved photo. It reads the stored listens of
those artists (bounded by the artist, not the date window, so the choice is stable across ranges),
the existing `artwork` / `artwork_name` album rows, and returns URLs. Nothing is persisted.
Selection policy lives in `domain.artwork.fallback`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Connection, text

from music_dashboard.domain.artwork.fallback import (
    VARIOUS_ARTISTS_MBID,
    VARIOUS_ARTISTS_NAMES,
    AlbumRef,
    AlbumStats,
    pick_cover,
)
from music_dashboard.infrastructure.artwork.urls import MBID, validate_image_url

# Same fold expression as the ranking queries (NFKC + full casefold map), so keys match exactly.
_FOLD = """
SELECT n.name, coalesce((SELECT string_agg(
   coalesce(CAST(:casefold AS jsonb)->>ch, ch), '' ORDER BY ord)
   FROM regexp_split_to_table(btrim(normalize(n.name, NFKC), :whitespace), '')
        WITH ORDINALITY AS chars(ch, ord)), '') AS folded
"""

_NAME_UNIVERSE = (
    """
WITH n AS (
  SELECT artist_name AS name FROM listen_artists
  WHERE artist_mbid IS NULL AND artist_name IS NOT NULL
  UNION SELECT artist_name FROM listens WHERE artist_name IS NOT NULL
)
SELECT name FROM ("""
    + _FOLD
    + """ FROM n) f WHERE 'name:' || folded = ANY(CAST(:keys AS text[]))
"""
)

# Listens credited to a wanted artist: by MBID credit, by unmapped credit name, or by the listen
# artist name when the listen has no credit rows at all (same identity the rankings use).
_CANDIDATES = """
SELECT l.id, l.artist_name, l.release_name,
  coalesce(l.release_group_mbid, l.release_mbid)::text AS kid,
  coalesce((SELECT jsonb_agg(jsonb_build_array(a.artist_mbid::text, a.artist_name))
            FROM listen_artists a WHERE a.listen_id = l.id), '[]'::jsonb) AS credits
FROM listens l
WHERE (l.release_group_mbid IS NOT NULL OR l.release_mbid IS NOT NULL
       OR l.release_name IS NOT NULL)
  AND l.id IN (
    SELECT listen_id FROM listen_artists WHERE artist_mbid = ANY(CAST(:mbids AS uuid[]))
    UNION SELECT listen_id FROM listen_artists
          WHERE artist_mbid IS NULL AND artist_name = ANY(CAST(:names AS text[]))
    UNION SELECT x.id FROM listens x WHERE x.artist_name = ANY(CAST(:names AS text[]))
          AND NOT EXISTS (SELECT 1 FROM listen_artists a WHERE a.listen_id = x.id))
"""

_VARIOUS = """(lower(btrim(l.artist_name)) = ANY(CAST(:va_names AS text[])) OR EXISTS (
  SELECT 1 FROM listen_artists a WHERE a.listen_id = l.id AND (a.artist_mbid = CAST(:va AS uuid)
  OR lower(btrim(a.artist_name)) = ANY(CAST(:va_names AS text[])))))"""

_ID_TOTALS = (
    """
SELECT coalesce(l.release_group_mbid, l.release_mbid)::text AS kid, count(*) AS total,
       bool_or("""
    + _VARIOUS
    + """) AS various
FROM listens l
WHERE coalesce(l.release_group_mbid, l.release_mbid) = ANY(CAST(:kids AS uuid[]))
GROUP BY 1
"""
)

_NAME_TOTALS = (
    """
SELECT l.artist_name, l.release_name, count(*) AS total, bool_or("""
    + _VARIOUS
    + """) AS various
FROM listens l
WHERE l.release_group_mbid IS NULL AND l.release_mbid IS NULL AND l.release_name IS NOT NULL
  AND l.artist_name = ANY(CAST(:names AS text[]))
GROUP BY 1, 2
"""
)

# Existing id-keyed history for the same raw artist names: canonical (artist, album) -> id.
_CANONICAL = """
SELECT DISTINCT artist_name, release_name, coalesce(release_group_mbid, release_mbid)::text AS kid
FROM listens
WHERE coalesce(release_group_mbid, release_mbid) IS NOT NULL AND release_name IS NOT NULL
  AND artist_name = ANY(CAST(:names AS text[]))
"""

_ID_COVERS = """
SELECT entity_mbid::text AS kid, image_url FROM artwork
WHERE entity_type = 'album' AND status = 'resolved'
  AND entity_mbid = ANY(CAST(:kids AS uuid[]))
"""

_NAME_COVERS = """
SELECT p.a AS artist, p.n AS album, w.image_url
FROM unnest(CAST(:artists AS text[]), CAST(:albums AS text[])) AS p(a, n)
JOIN artwork_name w ON w.entity_type = 'album' AND w.status = 'resolved'
 AND w.name_key = 'name:' || jsonb_build_array(p.a, p.n)::text
"""


def _folds(c: Connection, names: Iterable[str | None], params: dict[str, Any]) -> dict[str, str]:
    wanted = sorted({n for n in names if n})
    if not wanted:
        return {}
    rows = c.execute(
        text(_FOLD + " FROM unnest(CAST(:names AS text[])) AS n(name)"),
        {"names": wanted, "casefold": params["casefold"], "whitespace": params["whitespace"]},
    )
    return {r[0]: r[1] for r in rows}


def artist_cover_fallbacks(
    c: Connection, keys: Sequence[str], params: dict[str, Any]
) -> dict[str, str]:
    """Map ranking artist key (MBID or `name:` key) -> validated album cover URL, when found.

    `params` must carry the shared `casefold` / `whitespace` fold parameters."""
    mbids = sorted({k for k in keys if MBID.match(k)})
    name_keys = sorted({k for k in keys if k.startswith("name:")})
    if not mbids and not name_keys:
        return {}
    raw_names: list[str] = []
    if name_keys:
        raw_names = [
            r[0]
            for r in c.execute(
                text(_NAME_UNIVERSE),
                {
                    "keys": name_keys,
                    "casefold": params["casefold"],
                    "whitespace": params["whitespace"],
                },
            )
        ]
    cand = c.execute(text(_CANDIDATES), {"mbids": mbids, "names": raw_names}).mappings().all()
    if not cand:
        return {}
    wanted = set(mbids) | set(name_keys)
    artist_names = {r["artist_name"] for r in cand if r["artist_name"]}
    credit_names = {n for r in cand for _, n in r["credits"] if n}
    folds = _folds(
        c,
        artist_names | credit_names | {r["release_name"] for r in cand},
        params,
    )

    def credit_keys(row: Any) -> set[str]:
        credits = row["credits"]
        if not credits:
            f = folds.get(row["artist_name"] or "", "")
            return {"name:" + f} if f else set()
        out: set[str] = set()
        for mbid, name in credits:
            if mbid:
                out.add(mbid)
            elif folds.get(name or ""):
                out.add("name:" + folds[name])
        return out

    # Canonical album identity: name-only listens adopt a known id only when exactly one exists.
    canonical: dict[tuple[str, str], set[str]] = defaultdict(set)
    name_artists = sorted({r["artist_name"] for r in cand if r["kid"] is None and r["artist_name"]})
    if name_artists:
        hist = c.execute(text(_CANONICAL), {"names": name_artists}).all()
        hf = _folds(c, [n for h in hist for n in (h[0], h[1])], params)
        for a, n, kid in hist:
            if hf.get(a) and hf.get(n):
                canonical[(hf[a], hf[n])].add(kid)

    def album_ref(row: Any) -> AlbumRef | None:
        if row["kid"]:
            return AlbumRef(kid=row["kid"])
        af, nf = folds.get(row["artist_name"] or ""), folds.get(row["release_name"] or "")
        if not nf:
            return None
        ids = canonical.get((af, nf)) if af else None
        if ids and len(ids) == 1:
            return AlbumRef(kid=next(iter(ids)))
        return AlbumRef(artist=af or "", album=nf)

    credited: dict[str, dict[AlbumRef, int]] = defaultdict(lambda: defaultdict(int))
    for row in cand:
        ref = album_ref(row)
        if ref is None:
            continue
        for k in credit_keys(row) & wanted:
            credited[k][ref] += 1

    refs = {ref for per in credited.values() for ref in per}
    totals: dict[AlbumRef, tuple[int, bool]] = {}
    kids = sorted({r.kid for r in refs if r.kid})
    if kids:
        for r in c.execute(
            text(_ID_TOTALS),
            {"kids": kids, "va": VARIOUS_ARTISTS_MBID, "va_names": sorted(VARIOUS_ARTISTS_NAMES)},
        ):
            totals[AlbumRef(kid=r[0])] = (r[1], bool(r[2]))
    if any(not r.kid for r in refs):
        raw = sorted({r["artist_name"] for r in cand if r["artist_name"] and r["kid"] is None})
        rows = c.execute(
            text(_NAME_TOTALS),
            {"names": raw, "va": VARIOUS_ARTISTS_MBID, "va_names": sorted(VARIOUS_ARTISTS_NAMES)},
        ).all()
        tf = _folds(c, [n for r in rows for n in (r[0], r[1])], params)
        for a, n, total, various in rows:
            ref = AlbumRef(artist=tf.get(a, ""), album=tf.get(n, ""))
            old = totals.get(ref, (0, False))
            totals[ref] = (old[0] + total, old[1] or bool(various))

    covers: dict[AlbumRef, str] = {}
    if kids:
        for kid, url in c.execute(text(_ID_COVERS), {"kids": kids}):
            covers[AlbumRef(kid=kid)] = url
    pairs = sorted((r.artist or "", r.album or "") for r in refs if not r.kid)
    if pairs:
        for artist, album, url in c.execute(
            text(_NAME_COVERS),
            {"artists": [p[0] for p in pairs], "albums": [p[1] for p in pairs]},
        ):
            covers[AlbumRef(artist=artist, album=album)] = url

    result: dict[str, str] = {}
    for key, per in credited.items():
        stats = [
            AlbumStats(
                ref, max(totals.get(ref, (n, False))[0], n), n, totals.get(ref, (n, False))[1]
            )
            for ref, n in per.items()
        ]
        url = pick_cover(stats, covers, validate_image_url)
        if url:
            result[key] = url
    return result
