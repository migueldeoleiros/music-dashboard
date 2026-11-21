# Architecture and frozen semantics

## Boundaries

Single-user, public read-only POC: React/TypeScript + Vite, TanStack Query and scoped ECharts;
FastAPI/Pydantic; SQLAlchemy 2/Alembic; PostgreSQL. One worker, no Redis/Celery, microservices,
authentication, admin API, cover art or scrobble submission. ListenBrainz is authoritative;
the database can be rebuilt. No copied design assets. Inter is self-hosted under OFL.

Dependency direction:

```text
presentation -> application -> domain
infrastructure -> application ports / domain repository protocols
bootstrap -> concrete adapters (composition root)
```

Domain is stdlib-only, with an AST import-boundary test. Application ports describe use cases,
not a generic repository framework. Statistics must use SQL aggregates through
`StatisticsQueryPort`, never load all history into Python. Presentation never exposes raw
listens. API has no music-service credential configuration. Worker owns all upstream access.
API startup never runs migrations.

Only needed modules exist. Later agents add their application and infrastructure modules;
there are no empty use-case/repository placeholders. Discovery conventions remove shared-line
edits: API modules in `presentation/api/routes/` export `router`; CLI modules in
`presentation/cli_commands/` export `configure(subparsers)` setting an argparse `handler`;
job modules in `application/jobs/` export `tick()`. Modules must be import-side-effect free.
Worker calls jobs every 30 seconds; jobs own durable schedules/backoff and the single-worker
advisory lock. This host is not an implementation of the 15-minute sync schedule.

## Contract v1

Generated exclusively from FastAPI and Pydantic models in
`server/src/music_dashboard/presentation/api/schemas/`. Generation/staleness commands are in
the development guide. Schema changes require the integrator's coordinated decision and
regeneration of both artifacts before parallel workers consume them.

GET endpoints (prefix `/api`):

| Path | Response |
| --- | --- |
| `/rankings/{artists,albums,tracks,genres}` | ranked entity key/label/MBID/state, weighted listens/share |
| `/heatmap` | local daily all-listen cells, Monday-first |
| `/countries` | artist-associated ISO code, weighted listens/share |
| `/countries/{code}/artists` | RankingResponse of artists credited to one country; `{code}` is uppercase ISO alpha-2 from `COUNTRY_CODES` (anything else, incl. XW/XE/ZZ, is 400) |
| `/evolution/{artists,genres}` | local half-open buckets, whole-range top N series + Other |
| `/pies/{artists,genres}` | weighted slices + Other |
| `/health` | mode/status, last_updated, sync_lag_seconds, generation, coverage |

Braces above are documentation shorthand, except `/countries/{code}/artists`, the only real
path parameter: the contract contains ten code-free chart paths, that one, and one health path.
Country artists reuse the `country_credits` / `unique_credits` CTEs of `/countries` (1/N split
over unique country-resolved credits, XW/XE/ZZ excluded), so for identical filters each
country's item listens sum to its `/countries` listens, and `share` uses the same denominator
(country-resolved included listens), so item shares sum to the country's share. The response
cache key includes the code. Every chart request requires inclusive
ISO local-date `from`, `to`, and supported case-sensitive IANA `tz`; optional `limit`
is 1..100, default 10. Pies default top 10; evolution use-case default top 8 is a presentation
choice passed explicitly as `limit=8`. No all-time duration cap. Invalid dates, reversed dates,
unknown timezone and invalid limits yield 400 `{code,message,details}`, never 422. Missing
parameters also yield 400. Health requires no filters. No POST/PUT/DELETE/admin endpoints.
Automatic documentation UIs are disabled; aggregate-only OpenAPI remains available.

Every ranked entity carries `image_url: string | null` (default null). It stays null until
artwork enrichment (lane L-ART) fills it; clients render a placeholder tile for null.

### Artwork key rule

Images come only from exact identifier keys, never from name search. Unmapped `name:` keys and
genres have `image_url = null`.

- **Album**: the ranking key is the release-group MBID. Its representative release is the
  most-listened `release_mbid` within that release group in `listens` (ties broken by smallest
  mbid). Chain: Deezer `album/upc:{barcode of representative release}` -> Deezer URL relation on
  the representative release -> Cover Art Archive `release-group/{rg}/front` -> placeholder.
- **Track**: Cover Art Archive for the listen's own release (most-listened release of that
  recording) first, then Deezer `track/isrc:` -> placeholder. Compilation covers are a known risk.
- **Artist**: only through the MusicBrainz artist URL relation to a Deezer id (or a Wikidata
  Deezer artist id, P2722, reached through that relation) -> `artist/{id}`. Never "primary artist
  of an ISRC hit" (that is name matching). Otherwise placeholder.

### Artwork resolution and Deezer terms (lane L-ART)

Final chains (addendum 2026-11-22b) refine the rule above; exact keys only, never name or fuzzy
search. Album: Deezer `album/upc:{barcode}` or the Deezer URL relation on the representative
release, then CAA `release-group/{rg}/front`. Track: CAA front of the most-listened own release,
then Deezer `track/isrc:` album cover. Artist: MB Deezer URL relation, else Wikidata P2722
reached through the MB `wikidata` URL relation (first entity, redirects followed), then Deezer
`artist/{id}` `picture_xl`. Deezer error code 800 and an empty image hash (including
`d41d8cd98f00b204e9800998ecf8427e`) are misses; code 4 is quota and backs off.

- Table `artwork` (migration 0003) keyed `(entity_type album|track|artist, entity_mbid)` holds
  source, final https `image_url`, status (`resolved|miss|transient`), `fetched_at`,
  `next_retry_at`, `attempts`. URLs only: images are hotlinked by the browser, never downloaded
  or proxied. The CAA row stores the original `coverartarchive.org` URL, not the redirected
  storage host. The API role gets column-level SELECT on `entity_type, entity_mbid, image_url,
  status` only; the writer has full rights.
- The API returns an `image_url` only when status is `resolved` and the URL passes the validator
  (https, no userinfo/port, host exactly `cdn-images.dzcdn.net`, `e-cdns-images.dzcdn.net`,
  `coverartarchive.org`, `archive.org` or a true `*.archive.org` subdomain; lookalikes such as
  `evil-archive.org` or `archive.org.evil.com` are rejected).
- Artwork HTTP uses its own unauthenticated transport (no `Authorization`/cookie headers,
  descriptive `MUSIC_METADATA_USER_AGENT`, https and an allowlist of hosts and redirect targets).
  The ListenBrainz token never reaches MusicBrainz, Deezer, CAA or Wikidata.
- MusicBrainz artwork lookups share the enrichment process's single 1 request/second limiter and
  its 429/503/Retry-After handling. Deezer has its own token bucket (~5 req/s, burst 5; limit is
  50 per 5 s). Wikidata and CAA have separate small limiters.
- CSP: `deploy/proxy.conf` sets `Content-Security-Policy` on the HTML (`/`) location with
  `img-src` limited to `'self' data:` plus the five artwork hosts, `connect-src 'self'`,
  `script-src 'self'`, `frame-ancestors 'none'`. Fonts, charts and the map are bundled, so no
  `font-src`/`worker-src`/`blob:` extension is needed. nginx drops server-level `add_header` in a
  location that sets its own, so `nosniff` and `Referrer-Policy` are repeated in `/` and
  `/assets/`.

**Deezer terms risk (accepted by the user, 2026-11-22).** Deezer's terms of use
(developers.deezer.com/termsofuse, reviewed 2026-10-27) restrict making local copies (III.4) and
combining content with visuals (III.2), and contain an approved-developer clause. This personal
project accepts that risk to maximise cover coverage (spike: Deezer UPC 36% of albums, ISRC 86%
of tracks, artist URL relation 90%). Mitigations: store URLs only, hotlink in the browser, no
download/proxy/caching of image bytes, low request rate. If Deezer objects or blocks, fall back
to CAA-only by deleting `source='deezer'` rows and removing the Deezer steps; nothing else
depends on it.

All responses have coverage keyed by dimension: `artists, albums, tracks, genres, countries,
listens`, each `{total_listens,included_listens,excluded_listens}`; included + excluded = total.
Each chart's relevant dimension is authoritative; future adapters must fill every dimension
consistently. Shares use included listens, not total; empty denominator yields empty data.
Weights can be fractional while coverage counts whole listens. Responses carry generation
and nullable last_updated; health's unknown lag is null, never invented zero. Chart ranges
serialize `from/to/tz`, with canonical tz. `fixture=true` labels stub data. Health coverage is
all-history; chart coverage is the selected range. Generation is a monotonic invalidation
epoch; statistics agent must define a collision-free combination of ingestion/enrichment
counters (sum of monotonic counters is sufficient), never concatenate ambiguously.

## Identity, ingestion and reconciliation (T-003 constraints)

Source identity is `(user, listened_at epoch-second, recording_msid)`. Distinct MSIDs at the
same second remain distinct. Raw JSONB and provenance are retained; missing identity is
quarantined, not assigned a fabricated destructive key. Normalized rows store UTC timestamptz;
metadata remapping must never change source identity. Tombstones support upstream deletions.

LB pages are timestamp-only, nondeterministic ties, count <=1000 and one exclusive bound.
A full page ending at T must refetch `max_ts=T+1` to clear T before progressing to `max_ts=T`.
Full all-T responses are ambiguous and require authenticated export reconcile, never a false
complete checkpoint. Incremental default: every 15 minutes, 72-hour overlap (configurable).
Weekly full export reconcile repairs arbitrary old imports, edits, deletions and remaps.
Use advisory locking, durable checkpoints, bounded retries/backoff.

Stage exports and atomically activate only successfully parsed snapshots. Two notions:
import-complete (verified account via `user.json`, zero parse errors, rows >= the pre-export
`listen-count` lower bound) permits upsert and checkpoint advance but never tombstones;
deletion-safe requires a trusted snapshot manifest, which the real export status does not
carry, so real runs report `deletions_blocked`. Protect against incomplete scopes and >10%
deletion without explicit CLI override; the global count alone is never deletion proof.
Preserve post-snapshot arrivals; reconcile only snapshot scope under lock. Local rebuild and
upstream refresh are distinct operations. Verified real export shapes are in
`docs/development.md` ("Export completeness").

## Metadata and weighted semantics (T-004 constraints)

Start LB recording batch metadata with `inc=artist+tag+release+recording+release_group`.
Accept only recognized `genre_mbid` tags; never free tags such as seen-live. MB fallback uses
recording/release-group/artist `inc=genres`; one shared ~1 request/sec limiter, contact UA,
rate headers, durable retries. Distinguish transient failure and empty negative cache; proposed
TTLs 7 days empty / 30 days positive, configurable. Prioritize frequent entities.

First recognized level wins: recording -> release-group -> artist. At winning level,
positive votes are normalized proportionally, discarding nonpositives if positives exist;
recognized genres with no positives use equal shares. Artist fallback deduplicates MBIDs,
normalizes within each genre-resolved artist, shares equally among those artists, then
aggregates identical genres. Unresolved artists do not dilute resolved shares. Apply newest
enrichment to all history (not historical truth); increment enrichment generation.

Country is associated credited artist ISO country, not nationality/birthplace or guessed area.
Deduplicate artist MBIDs; split among country-resolved credited artists; aggregate countries.
One resolved artist includes the listen. Unknown/XW/XE omitted, never an Unmapped map bucket;
partial-credit note is explicit. Licensed Natural Earth ISO-keyed map asset belongs to T-006.

## Statistics, date and display semantics (T-005/T-006 constraints)

Keep identifiable unmapped entities in ranks and artist charts; explicit missing-title/album
state rather than dropping listens. Artist keys: resolved MBID, else Unicode-normalized
casefold composite with prefix. No arbitrary feat/& splitting, no blind ambiguous-name merge.
Only unique unambiguous aliases may resolve fallback names; document that policy in T-005.
Albums prefer release-group, then release, then artist+album fallback identity.
All listens count in heatmap. Omit unresolved genre only from genre views, unresolved country
only from map. Genre/country weights sum to included count within tolerance. Other is known
categories outside top N, never unknown metadata. Evolution picks top 8 across whole range,
zero-fills daily/weekly/monthly buckets, noncumulative, marks partial final bucket. Pies top10.

Visitor timezone is browser Intl default, explicit override/reset, UTC fallback. URL shares
absolute from/to/tz, not immutable snapshots. Controlled timezone alias metadata is bundled
from tzdata 2025b (PyPI 2025.2), shared by request validation and database seed. PostgreSQL is
sole authority for date boundaries and buckets: inclusive local from through day after local
to becomes half-open UTC range, DST-aware; no Python boundary math. Day is finest bucket;
one-day and all-time ranges supported. Parameterize SQL. Adapt bucket granularity, don't cap
all-time away. T-005 sets a measured bucket budget and bounded TTL/LRU cache keyed by canonical
filters + generation. API role statement_timeout=5s; nginx limits 10 requests/sec, burst20.
Public aggregates can reveal listening patterns, especially narrow ranges/timezone comparisons;
there is no privacy guarantee.

## UI lane ownership

| Lane | Owns |
| --- | --- |
| C1 shell/theme | `styles/global.css` token values, `styles/shell.css`, `app/App.tsx`, `features/dashboard/{Controls,filters}`, `components/ui/{SyncStatus,CoverageNote}`, `tests/App.test.tsx` |
| C2 charts | `components/charts/*`, `styles/charts.css`, `tests/charts.test.tsx`; `features/dashboard/{HeatmapPanel,MapPanel}.tsx` bodies |
| C3 entity-list/rankings/country panel | `components/ui/{EntityImage,EntityRow,EntityList}`, `styles/entity-list.css`, `features/dashboard/RankingList.tsx`, `features/rankings/`, `features/origin-map/CountryArtistsPanel` |
| L-ART server artwork + deploy | server artwork migration/jobs/adapters, artwork LEFT JOINs in ranking and country-artist queries, `deploy/proxy.conf` (CSP) |

`features/dashboard/ChartSection.tsx` (generic frame) and `ChartPanel.tsx` (thin dispatcher) are
shared; change them only in the integration phase. Token names: see `docs/ui-tokens.md`.

## Ownership after foundation merge

| Task | Owns (may add modules under listed directories) |
| --- | --- |
| T-003 sync | `domain/listening/`; `application/sync_listens.py,reconcile_history.py`; `application/jobs/sync*.py`; `infrastructure/listenbrainz/` export/listen adapters; `infrastructure/persistence/repositories/listening*.py,sync*.py`; `presentation/cli_commands/sync*.py,reconcile*.py,import*.py`; sync-specific tests |
| T-004 enrichment | `domain/metadata/`; `application/enrich_metadata.py`; `application/jobs/enrichment*.py`; `infrastructure/musicbrainz/`; `infrastructure/listenbrainz/metadata.py`; `infrastructure/persistence/repositories/metadata*.py`; `presentation/cli_commands/enrich*.py`; enrichment-specific tests |
| T-005 statistics | `domain/statistics/`; `application/get_statistics.py`; `infrastructure/persistence/statistics_queries.py`; `presentation/api/routes/statistics.py,health.py` and `dependencies.py`; statistics-specific tests |
| T-006 client | all `client/` except generated contract changes require integration approval; Natural Earth asset/license; chart/frontend tests |
| Integrator only | `application/ports.py`, frozen API schemas, `contracts/`, bootstrap/config/app/worker/CLI hosts, database models and migrations, timezone metadata, fixture seed/data and shared test harness, all compose/deploy files, shared docs, server dependency manifests/locks |

T-003 owns LB transport/rate-limiter shared module; T-004 implements only metadata adapter on
the agreed transport port, coordinating any shared transport change through integrator.
T-003/T-004 may not edit shared `__init__` files to register plugins. Tests use task-specific
filenames; no concurrent edits to shared conftest or fixtures. T-005 requests bootstrap switch
from fixture adapter through integrator. Contracts/repository model changes require explicit
coordination, not silent drift. Each agent branches from merged foundation on its own worktree;
coordinator merges sequentially into main. No feature branch merges/pushes by workers.
