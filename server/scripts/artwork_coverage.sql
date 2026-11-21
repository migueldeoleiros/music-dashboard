-- Read-only artwork coverage report. Run:
--   docker compose exec -T db psql -U music_writer -d music_dashboard -X \
--     -v ON_ERROR_STOP=1 < server/scripts/artwork_coverage.sql
-- Mirrors the ranking keys: album = coalesce(release_group, release, name), track = recording.
BEGIN READ ONLY;
\echo '== artwork rows by type/status/source'
SELECT entity_type, status, source, count(*), sum(attempts) AS attempts
FROM artwork GROUP BY 1,2,3 ORDER BY 1,2,3;
\echo '== album keys: kind, listens, with image'
WITH k AS (
  SELECT coalesce(release_group_mbid::text, release_mbid::text,
           'name:' || lower(coalesce(artist_name, '')) || '|' || lower(coalesce(release_name, ''))) AS key,
         CASE WHEN release_group_mbid IS NOT NULL THEN 'release-group'
              WHEN release_mbid IS NOT NULL THEN 'release' ELSE 'name (no id)' END AS kind,
         count(*) AS n
  FROM listens GROUP BY 1, 2)
SELECT kind, count(*) AS keys, sum(n) AS listens,
       count(*) FILTER (WHERE a.status = 'resolved') AS with_image
FROM k LEFT JOIN artwork a ON a.entity_type = 'album' AND a.entity_mbid::text = k.key
GROUP BY 1 ORDER BY 1;
\echo '== track keys'
WITH k AS (SELECT coalesce(recording_mbid::text, 'name:' || lower(coalesce(artist_name, '')) || '|'
                  || lower(coalesce(track_name, ''))) AS key, count(*) AS n
           FROM listens GROUP BY 1)
SELECT CASE WHEN key LIKE 'name:%' THEN 'name (no id)' ELSE 'recording' END AS kind,
       count(*) AS keys, sum(n) AS listens,
       count(*) FILTER (WHERE a.status = 'resolved') AS with_image,
       count(*) FILTER (WHERE a.status = 'miss') AS miss,
       count(*) FILTER (WHERE a.status = 'transient') AS transient
FROM k LEFT JOIN artwork a ON a.entity_type = 'track' AND a.entity_mbid::text = k.key
GROUP BY 1 ORDER BY 1;
\echo '== LB raw CAA hints (caa_release_mbid + caa_id) vs listen release'
SELECT (m.mm->>'caa_release_mbid') IS NOT NULL AS has_hint,
       (m.mm->>'caa_release_mbid') = l.release_mbid::text AS hint_is_listen_release,
       count(*) AS listens
FROM listens l
JOIN raw_listens r ON r.user_name = l.user_name AND r.listened_at = l.listened_at
  AND r.recording_msid = l.recording_msid AND r.tombstoned_at IS NULL
CROSS JOIN LATERAL (SELECT r.payload->'track_metadata'->'mbid_mapping' AS mm) m
GROUP BY 1, 2 ORDER BY 1, 2;
\echo '== resolved URL shapes (host)'
SELECT entity_type, source, substring(image_url from '^https://[^/]+') AS host, count(*)
FROM artwork WHERE status = 'resolved' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
ROLLBACK;
