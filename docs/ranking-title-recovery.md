# Ranking title recovery

## Scope and precedence

Recovery is label-only and limited to album/track rankings. It does not rewrite listens,
change album identity, artwork selection, artist credits, counts, total plays, coverage,
pies, or evolution charts. Explicit selected labels and their existing tie selection win.

Blank album buckets use consistent exact release evidence first. Stored titles take
precedence over cached titles for the same identity; conflicts are not resolved by the
cache. Release-group evidence is eligible only in the absence of exact-release evidence,
not when that evidence conflicts. Track recovery uses exact recording-MBID title history
only. Unresolved albums are removed before ranking/limit/Other; album shares use all named
groups before the limit. A badge describes the resulting label.

## Persistence and permissions

Migration ordering is linear: `0006 -> 0005 -> 0004`. `0005` belongs to public refresh;
its dependency commit is `7265ae4bfd6652cdb46ca8b73cd52a73e27f3ab0`, imported locally as
`371ec74`. Integration must apply that dependency once, before the recovery commit.
No prior artwork branch is required or imported.

`release_title_cache(entity_type, entity_mbid, title)` is the positive title projection.
The API receives column-level SELECT only on those three columns. Lookup scheduling,
negative results, retry attempts, and provenance remain in worker-only `release_title_lookup`. API requests never
perform MusicBrainz lookups. The additive partial `ix_listens_release_mbid` index supports
exact-release history probes; recording/release-group probes use their existing indexes.
Lookup identities must be deduplicated before probing history.

Title publication and an atomic `enrichment_generation = enrichment_generation + 1` happen
in the same short transaction when a published title changes. Publication does not change
`ingestion_generation` or `last_updated`. Negative/retry bookkeeping alone must not bump
the generation. Failed refreshes retain previously published positive titles. Network I/O
never runs inside a database transaction; the enrichment session advisory lock coordinates
worker runs. No title-enrichment operation writes listens.

## Worker bounds

Title lookup runs after metadata enrichment under its existing session advisory lock.
Fixture mode prevents upstream access. Non-fixture workers require an identifying
`MUSIC_METADATA_USER_AGENT`. `MUSIC_TITLE_LOOKUP_BUDGET` defaults to 25 requests per run;
`MUSIC_TITLE_LOOKUP_DEADLINE_SECONDS` defaults to 120 seconds. Positive titles refresh after
30 days; not-found results retry after seven days. Transient errors stop the run and use
exponential backoff from 30 seconds to one day, honoring upstream Retry-After and a durable
source cooldown (lookup status tracks the latest attempt; the title client persists the longest of Retry-After and rate-limit reset, clamped to one day; shared genre parsers are unchanged). Title HTTP requests run in a spawned child process whose wait is bounded by the run deadline (child killed on expiry; covers DNS/headers/body slow-drip) with a 256 KiB body cap. Residuals, not hard guarantees: the post-ready local pipe read is not time-bounded, and cleanup is a kill plus a 1s join. A transient refresh retains the last published positive title.

## Safety and verification

All write verification uses explicitly disposable PostgreSQL databases, never the deployed
stack. The integration fixture destroys the target public schema: do not point
`TEST_DATABASE_URL`, `TEST_API_DATABASE_URL`, or `TEST_WRITER_DATABASE_URL` at live data.

Required proof includes real API/writer role behavior, the combined migration chain,
rollback/re-upgrade with the refresh schema intact, concurrent refresh/title publication,
unchanged ingestion generation and last-updated time, and narrow/wide date reproduction.
Measure uncached EXPLAIN ANALYZE and wall timing for empty miss, sparse miss, and wide
ranges; a sparse miss must use indexed exact history probes rather than scan history.
Performance evidence uses synthetic data and must be reported separately from any live
reproduction, which is not authorized by this implementation run.

No deployment, merge, or live writes are authorized. Recovery is delivered as a separate
commit after the refresh dependency and awaits coordinator merge instructions.
