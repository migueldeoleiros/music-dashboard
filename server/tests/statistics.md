# T-005 statistics implementation notes

## Wiring and ownership

Owned API dependencies select `SQLStatisticsQueries` when `MUSIC_FIXTURE_MODE=false`.
`true` preserves the foundation's explicit offline contract demonstration adapter.
No bootstrap, schema, migration, fixture, contract, shared port or deployment file changed.
Set live mode even when querying seeded synthetic database fixtures.

Runtime URL is `settings.database_url`; Compose supplies `music_api`, not writer/admin.
Queries use read-only repeatable-read snapshots, a local 5s statement timeout, bound
parameters, SQL aggregates, and generation-aware response/coverage caches.

**Integration invariant:** ingestion/enrichment counters must only increase, in the
same transaction as derived-data changes. Their sum is the invalidation generation.
`data_generation.last_updated` must remain **ingestion freshness**, not be advanced
by enrichment alone; the frozen schema has no separate API-readable sync timestamp.
No raw/sync/cache tables are read to manufacture health freshness.

## Explicit policies

- PostgreSQL does all date membership/boundary/bucket work. A coarse indexed UTC
  prefilter plus exact local-date membership handles ambiguous/repeated midnight,
  skipped midnight and skipped dates. Both occurrences of Havana midnight count
  on the same local day, never on the preceding day.
- Names use NFKC + full Unicode casefold + whitespace trimming. PostgreSQL 17 lacks
  casefold, so a bound character mapping is applied to distinct normalized names.
  No Python hydration of history, no database extension/migration needed.
- Never resolve a submitted name to an MBID via an alias guess. Distinct explicit
  MBIDs remain distinct even with identical labels. Submitted feat/& credits stay
  intact unless ingestion supplied actual credited artist rows.
- Unique credited artists share a listen equally; repeated MBIDs deduplicate.
  Name-identifiable unmapped artists remain included. Missing artist names are
  excluded from artist views and their coverage, not rolled into Other. Album and
  track missing metadata remain explicit ranked states and count as included.
- Country whitelist is ISO 3166-1 alpha-2, including territories; XW/XE/ZZ and
  invented codes are omitted. Genre precedence and positive/equal vote policies
  (pies, evolution) are computed from resolution tables, not raw metadata/cache. Genre
  rankings alone count 1 per distinct genre per listen (votes ignored, duplicates collapsed),
  shares over total appearances; coverage counts unique listens.
- Heatmap cells are sparse, ordered daily totals; absent dates mean zero. Monday
  is the declared week start. This permits all-time without millions of empty cells.
- Evolution uses daily (<90 days), Monday-weekly (<730 days), otherwise monthly
  buckets. Normal custom ranges retain their zero-filled empty edges. Only extreme
  bounds 0001-01-01 / 9999-12-31 trim empty edges to actual history (all-time).
  Final bucket labels are clipped to the exclusive requested end, with a partial
  flag computed against the natural calendar boundary. Empty DB yields no buckets.
- Pies and evolution ignore `limit`. Pies: named entries with >=1% share of included listens (exactly 1% kept), remaining mass in Other, no cap; evolution: the same >=1% rule (at most 100 series + Other). Rankings
  keep limit 1..100 (default 10). Other contains known tail only.
- Evolution budget: <=120000 buckets and <=2000000 values (actual series × buckets),
  checked before allocating series arrays; each zero array is allocated once.
  Excess custom combinations yield typed400; all-time ordinary history is allowed.
  Literal data in year9999 cannot produce an exclusive year10000 Python date label
  and returns typed400 for evolution rather than overflowing the public schema.
- Response cache: 30s TTL, 128-entry LRU, 16MiB serialized byte budget. Covers chart
  responses and reusable per-range coverage. Health recomputes lag but caches coverage.
  Health is degraded before any import or when ingestion freshness is >24h old;
  unknown lag remains null. These are POC display policies, not a sync SLA.

## Verification

Author commands, from `server/`:

```
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest tests/unit -q
.venv/bin/music-dashboard contract --check
.venv/bin/python scripts/check_db.py
```

Latest author run: lint/typecheck/contract passed; 30 unit and32 integration tests
passed, one existing Starlette/AnyIO deprecation warning. Database script uses an
isolated PostgreSQL17.7 container and removes it. Candidate-id command unavailable;
these are session measurements, not fabricated receipt-bound signoff.

Measured cold requests, separate adapter/cache per family, 100030 synthetic listens
(including100000 hourly extension rows over ~11 years, 1000 artist aliases,
10000 track names and1000 album names; predominantly repeated resolved MBIDs):

| Family | Wall time ms |
| --- | ---: |
| rankings/artists | 3290.041 |
| pies/genres | 3728.281 |
| evolution/genres | 5636.268 |
| heatmap | 2610.618 |
| health cold | 1718.612 |
| health warm | 2.209 |

Evolution uses several individually timeout-bounded statements, hence total request
wall time can exceed5s. A 40-request seeded random family/range/timezone run on the
30-listen baseline measured median11.730ms, max30.370ms, total518.304ms.
No SLA claimed. High-diversity metadata, much larger histories, concurrency and
production resources still require coordinator measurement. Load test initially
found a quadratic nested join; replacing genre entity joins with partition windows
removed that failure without raising the statement timeout.

Compose smoke used project `md-stats`, loopback18130, live mode and a temporary
image-name override (`md-stats-server:local`) to avoid other worktrees' image tags.
Build, migrations, fixture seed, up--wait succeeded; all ten HTTP chart paths and
health returned200 live, balanced coverage. PostgreSQL reported current_user
music_api, timeout5s, and false SELECT privileges for raw_listens/sync_state.
No upstream music-service calls or tokens used. Compose is stopped before handoff.

Normalized fixture reconstruction is tested with unchanged raw/cache rows and
identical aggregate output (except freshness). Actual raw+cache replay belongs to
T-003/T-004 integration and remains a coordinator gate, not implemented here.
