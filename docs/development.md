# Development

## Chosen tools and locks

Machine inspection found Python 3.14.10 (no global pip/uv), Node 24.22.2, npm 11.19.1,
pnpm, and working Docker/Compose. We chose Python venv + uv 0.9.11 and **npm**, not pnpm.
Python supports >=3.12; the server image uses 3.14. Client image uses Node 22; local Node 24
also passes. Direct dependencies are exact-pinned, with `server/uv.lock` and
`client/package-lock.json` pinning transitives. Use frozen installs, not ad-hoc upgrades.

From repository root:

```sh
python -m venv server/.venv
server/.venv/bin/python -m pip install uv==0.9.11
(cd server && .venv/bin/uv sync --frozen)
(cd client && npm ci)
```

Tests and fixture mode make no ListenBrainz/MusicBrainz calls. Never paste,
print or commit a ListenBrainz token. Never run `docker compose config` without `--quiet`
when credentials are configured: expanded environment values can be printed.

## Local Compose (synthetic mode)

Copy `.env.example` to ignored `.env`. Set three **different**, locally generated URL-safe
passwords: MUSIC_ADMIN_PASSWORD, MUSIC_WRITER_PASSWORD, MUSIC_API_PASSWORD. Keep the file
mode 0600. Leave LISTENBRAINZ_USERNAME/TOKEN empty, MUSIC_FIXTURE_MODE=true and
MUSIC_WORKER_FIXTURE_MODE=true.
The administrator password goes only to db; writer credentials to worker; API only gets its
own read-only credentials. Do not reuse these passwords elsewhere.

```sh
cp .env.example .env
chmod 600 .env
# Set the database passwords privately in your editor before proceeding.
docker compose config --quiet
docker compose build
docker compose up -d db --wait
docker compose run --rm worker alembic upgrade head
docker compose run --rm -e LISTENBRAINZ_TOKEN= worker music-dashboard seed-fixtures
docker compose up -d --wait
curl --fail http://127.0.0.1:8080/api/health
curl --fail 'http://127.0.0.1:8080/api/pies/genres?from=2026-01-05&to=2026-01-05&tz=UTC&limit=1'
```

WEB_PORT overrides 8080 (verification used 18080). Web is loopback-only. DB and worker have
no published ports. Compose `up --wait` establishes process liveness, **not** schema/seed
readiness: always run the explicit migration/seed sequence above. The DB probe uses TCP to
avoid the temporary init socket server. In fixture mode API health is synthetic liveness
(`mode=fixture`) and the populated examples are contract demonstrations, not statistics
computed from the seeded DB. With API `MUSIC_FIXTURE_MODE=false` the API serves SQL aggregates
from PostgreSQL (synthetic seeded data stays usable this way, no upstream calls) and
`/api/health` returns 503 when statistics are unavailable. Upstream access is controlled
separately by the worker flag `MUSIC_WORKER_FIXTURE_MODE` (default true = never calls upstream).

Role initialization runs once on an empty volume. Changing .env does not rotate passwords on
an existing volume. Apply role/password changes privately with an administrator, or use a
separate fresh development volume. Never delete a volume containing wanted data.

Inspect seed/schema without exposing private payloads:

```sh
docker compose exec -T db psql -U music_writer -d music_dashboard -c 'SELECT count(*) FROM listens'
docker compose exec -T db psql -U music_api -d music_dashboard -c 'SELECT count(*) FROM listens'
# This is expected to fail with permission denied:
docker compose exec -T db psql -U music_api -d music_dashboard -c 'SELECT count(*) FROM raw_listens'
docker compose ps
docker compose down
```

## Larger synthetic SQL-backed stack

Use a **fresh, isolated disposable project**; do not mix either seed with real history.
Stop every writer before seeding. The seed preflight rejects foreign rows, but is not a
concurrency lock. Keep worker fixture mode true throughout this recipe.

```sh
(
export COMPOSE_PROJECT_NAME=md-integ WEB_PORT=18150
export MUSIC_FIXTURE_MODE=false MUSIC_WORKER_FIXTURE_MODE=true
docker compose config --quiet
docker compose build
docker compose up -d db --wait
docker compose stop worker
docker compose run --rm worker alembic upgrade head
docker compose run --rm worker python scripts/seed_integration.py --confirm-synthetic
docker compose run --rm worker python scripts/seed_integration.py --verify
docker compose up -d --wait
curl --fail 'http://127.0.0.1:18150/api/pies/genres?from=0001-01-01&to=2026-10-31&tz=UTC'
# Local disposable recovery/security exercise; creates a fresh temporary restore DB.
python server/scripts/verify_stack.py --project md-integ --burst
docker compose down
)
```

The deterministic seed uses real normalization and cache projection paths, no network:
8,000 listens (2018–2026), 40 artists, 20 genres, collaborations, missing/unmapped metadata,
20 country codes plus XW (excluded from maps), six quarantined rows. Re-running is idempotent.
UTC coverage scenarios: 2018–2019 = zero; 2020–2022 = partial; **2026-10-31 = 100% genre and
country coverage (6/6)**. The 2023–2026 era has all entities processed, not all listens resolved:
negative cache/unmapped entries still reduce coverage. `--plan` needs no DB; `--skip-enrichment`
only means zero coverage on a fresh DB. `--include-transient` is opt-in and makes strict
raw/cache rebuild unavailable (see below). Never run these scripts against production.

All time deliberately sends the supported `0001-01-01` bound: SQL trims evolution's empty
edges to history before allocating monthly buckets; sparse heatmap cells include actual
history only. There is no earliest-history endpoint to query instead. The client calendar
starts at the first returned listen and shows one year at a time, including quiet years.

## Real-data first run

Requires local ListenBrainz username and user token (from your LB profile settings). Put them
only in ignored `.env`; never paste them in chat, shell history, issues or logs.

**Use a fresh stack for real data.** `seed-fixtures` inserts `fixture_user` listens and
statistics SQL aggregates all listens (no per-user filter), so seeded fixtures would mix into
real charts. Skip `seed-fixtures`; if already seeded, start from a new disposable volume
(`docker compose down -v` deletes the volume: only do that when nothing there is wanted).

`.env` for real mode:

```sh
MUSIC_FIXTURE_MODE=false            # API: SQL aggregates
MUSIC_WORKER_FIXTURE_MODE=false     # worker: allow real upstream jobs
LISTENBRAINZ_USERNAME=<your LB username>
LISTENBRAINZ_TOKEN=<set privately in editor>
MUSIC_METADATA_USER_AGENT='music-dashboard/0.1 (mailto:you@example.org)'
```

The worker container receives `MUSIC_WORKER_FIXTURE_MODE` (as its fixture flag), `LISTENBRAINZ_*`,
`MUSIC_METADATA_USER_AGENT` and the optional `MUSIC_SYNC_*`/`MUSIC_METADATA_*`/
`MUSIC_ENRICHMENT_BATCH_SIZE` settings from `.env` through compose. Real jobs are enabled
only by explicit `MUSIC_WORKER_FIXTURE_MODE=false`. After changing it, recreate the worker
(`docker compose up -d --force-recreate worker`); `docker compose run` picks up `.env` anew.
The server image tag is project-scoped (`${COMPOSE_PROJECT_NAME:-music-dashboard}-server:local`).

```sh
docker compose build
docker compose up -d db --wait
docker compose run --rm worker alembic upgrade head
# 1. Authenticated full export (creates an LB export, polls for status "completed", downloads
#    the ZIP from /1/export/{id}/download). May take minutes. Succeeds on import-complete
#    evidence: imports and checkpoints, but never deletes (see "Export completeness").
docker compose run --rm worker music-dashboard bootstrap-import
# 2. Recent incremental sync (page API, 72h overlap) - also run by the worker every 15 min.
docker compose run --rm worker music-dashboard sync
# 3. Enrichment, explicit batches. --allow-network is required; limit 1..1000.
docker compose run --rm worker \
  music-dashboard enrich-metadata --allow-network --limit 100
# 4. Start everything; the worker then syncs and enriches continuously (~1 req/s limiter).
docker compose up -d --wait
curl --fail http://127.0.0.1:8080/api/health
```

Each sync command prints one JSON stats object (counts only, no payloads/credentials).
Failures print a generic actionable message. Open http://127.0.0.1:8080 for the dashboard.
Initial enrichment can take hours or days; charts show partial coverage meanwhile, and
unresolved genres/countries are excluded from those views only (coverage notes show
total/included/excluded). Genre rankings count one full appearance per distinct genre per
listen (votes ignored), so their total can exceed covered listens; genre pies and evolution stay
vote-weighted. Do not run commands in a loop to hurry enrichment or probe LB.

Other commands: `music-dashboard reconcile` (re-run the export; imports old edits and remaps,
and deletes only with a trusted snapshot manifest, which the real service does not provide;
required after an `ambiguous` boundary: >1000 listens in one second),
`reconcile --allow-large-delete` / `bootstrap-import --allow-large-delete` (explicit override
of the >10% deletion guard; use only after verifying the drop is real). Exact reconcile:

```sh
docker compose run --rm worker music-dashboard reconcile
# ONLY after completeness and a genuine >10% deletion have been independently verified:
docker compose run --rm worker music-dashboard reconcile --allow-large-delete
```

Watch count/status-only progress (never dump raw payloads or private environment):

```sh
docker compose logs --tail 50 worker
curl --fail http://127.0.0.1:8080/api/health
docker compose exec -T db psql -U music_writer -d music_dashboard -c \
  'SELECT id, kind, status, started_at, finished_at, error FROM sync_runs ORDER BY id DESC LIMIT 10'
docker compose exec -T db psql -U music_writer -d music_dashboard -c \
  'SELECT status, count(*) FROM enrichment_queue GROUP BY status ORDER BY status'
docker compose exec -T db psql -U music_writer -d music_dashboard -c \
  'SELECT user_name, last_export_at, last_incremental_at, requires_reconcile FROM sync_state'
```

Dashboard coverage notes report total/included/excluded; health reports ingestion freshness,
not enrichment completion. Worker failures deliberately omit unsafe exception details.

### Export completeness: verified shapes and the two-notion policy

Verified against the real service (first real run; no token or payload values recorded):

- `POST /1/export/` creates an export (`export_id`); `GET /1/export/{id}` returns
  `available_until, created, end_time, export_id, filename, progress, start_time, status, type`.
  The ready value is `"status": "completed"`. There is **no** `listen_count` and **no**
  `download_url`.
- Download: `GET /1/export/{id}/download` (authenticated, `application/zip`). The adapter
  never follows a status-supplied URL.
- ZIP layout: `user.json` (`user_id` int, `username` str), `listens/YYYY/MM.jsonl`, and
  `feedback.jsonl` (ignored, like every unknown member). Only `user.json` and
  `listens/**/*.jsonl` are read.
- Rows: `listened_at` is a **float** (`1790856000.0`), `recording_msid` present, `mbid_mapping`
  may be null. Identity stays `(user, listened_at, recording_msid)`, keyed on the exact integer
  epoch second.
- `GET /1/user/{user}/listen-count` -> `{"payload": {"count": N}}`; it matched the export row
  count exactly for the probed account.

Timestamps: ints and finite, integral, in-range floats map to the exact stored int epoch.
NaN/inf, negative, non-integral, out-of-range values and decimals that would round as floats
(e.g. `...000.0000000001`) never become an identity: the row is kept in raw quarantine and
blocks deletions; nothing is invented or rounded. The incremental page API returns int
`listened_at`; ints only are accepted there.

Two distinct notions of completeness:

1. **Import-complete** (what real exports can prove): `user.json` `username` equals the
   configured user (case-insensitive), the archive parsed with zero fatal errors (CRC, JSON,
   duplicate identity, size/member limits), and exported rows >= the `listen-count` fetched
   immediately before the export was created (rows may exceed it: listens can arrive meanwhile).
   Import-complete lets the run upsert rows, update remaps, advance the checkpoint (clearing
   an ambiguous-boundary flag, since the import covers those rows) and succeed. It **never
   tombstones** and never marks deletion-safe. Fewer rows than the lower bound fails the run
   before anything is activated.
2. **Deletion-safe** (unchanged strict rule): only a trusted snapshot manifest (status
   `listen_count`, never observed on the real service) together with a full row-count match,
   zero invalid identities and the >10% guard can tombstone. The global `listen-count` alone is
   never deletion proof.

Consequence with real statuses: `bootstrap-import`/`reconcile` import and succeed, report
`deletions_blocked: 1`, `tombstoned: 0`, `import_complete: 1`, `deletion_safe: 0` in the run
stats, and delete nothing. Upstream deletions are therefore **not** reflected locally until a
trusted manifest exists; this is a documented limitation, not a bug. Operator notes:

- Inspect the latest `sync_runs` stats (counts only) to see these flags. Do not edit rows by hand.
- A corrupt, empty, mismatched, wrong-account or oversized export is rejected before any data is
  activated; existing history is untouched. Rows from an attempt that fails after activation
  cannot exist: activation is atomic.
- `--allow-large-delete` bypasses only the percentage guard, **never** completeness validation.
  Do not manufacture `listen_count`, edit checkpoints or disable deletion protections.

## Artwork

Resolves `image_url` for albums (release group MBID), tracks (recording MBID) and artists (artist
MBID) from exact identifiers first, then (migration 0004) a Deezer name-search fallback, also for
name-only `name:` ranking entities; see "Artwork resolution and Deezer terms" and "Artwork name
fallback" in architecture.md. Needs migration 0004 (`alembic upgrade head`) and `MUSIC_WORKER_FIXTURE_MODE=false`
with a descriptive `MUSIC_METADATA_USER_AGENT`. Tests mock all HTTP; fixture mode makes none.

```sh
# Manual bounded batch (limit 1..1000, default 20). --allow-network is required.
docker compose run --rm worker \
  music-dashboard resolve-artwork --allow-network --limit 100
# Retry every miss/transient row now (resolved rows keep their 30-day TTL), then resolve:
docker compose run --rm worker \
  music-dashboard resolve-artwork --allow-network --reset-misses --limit 100
docker compose exec -T db psql -U music_writer -d music_dashboard -c \
  'SELECT entity_type, status, source, count(*) FROM artwork GROUP BY 1,2,3 ORDER BY 1,2,3'
```

- **Coverage report** (read-only, safe on the live DB): `docker compose exec -T db psql -U
  music_writer -d music_dashboard -X < server/scripts/artwork_coverage.sql` shows album/track key
  kinds vs images, misses/transients, LB CAA hints and URL host shapes.
- **Job**: `application/jobs/artwork.py` (`tick()` every 30 s, discovered automatically; inert in
  fixture mode). While the genre enrichment queue has due work it runs a reduced share (every 4th
  tick, 5 entities); when the queue is idle it runs up to 20 entities per tick
  (`MUSIC_ARTWORK_BATCH_SIZE`, `MUSIC_ARTWORK_BUSY_BATCH_SIZE`, `MUSIC_ARTWORK_BUSY_EVERY`).
  Entities are taken by listen frequency, so the most-listened items get images first.
- **TTLs** (all +/-20% jitter except transient): resolved 30 days
  (`MUSIC_ARTWORK_POSITIVE_TTL_DAYS`), miss 7 days (`MUSIC_ARTWORK_MISS_TTL_DAYS`), transient
  exponential from 60 s capped at 24 h (`MUSIC_ARTWORK_RETRY_BASE_SECONDS`,
  `MUSIC_ARTWORK_RETRY_MAX_SECONDS`), at least any `Retry-After`. A failed refresh never discards a
  resolved image. Deezer quota (code 4) pauses artwork in that process (10 s doubling to 15 min).
- **Representative release**: albums use the most-listened `release_mbid` in the release group,
  tracks the most-listened release of that recording; ties go to the smallest MBID.
- **Reset misses**: `--reset-misses` as above, or as writer
  `UPDATE artwork SET next_retry_at = now() WHERE status IN ('miss','transient')`. To force a full
  re-resolve of everything: `UPDATE artwork SET next_retry_at = now()` (images stay visible until
  replaced). Deleting rows also works. New images appear after the generation bump (the job bumps
  `enrichment_generation` when a visible image changes) and the API cache TTL.
- **Name fallback backfill** (after deploying 0004; no deploy done by this branch): 1) migrate;
  2) name-only entities have no row, so they are due immediately, most-listened first;
  3) existing MBID `miss`/`transient` rows must be re-tried once to get the fallback: run
  `resolve-artwork --allow-network --reset-misses --limit 100` (now also resets `artwork_name`),
  then repeat WITHOUT `--reset-misses` until counts stop growing (resolved rows keep their TTL;
  exact-chain images are never replaced by name matches). Check provenance as writer:
  `SELECT entity_type, match_kind, status, count(*) FROM artwork_name GROUP BY 1,2,3` and
  `SELECT match_kind, count(*) FROM artwork GROUP BY 1`. To discard all name matches (e.g. after
  spotting bad ones): `DELETE FROM artwork_name;` and
  `DELETE FROM artwork WHERE match_kind <> 'id';` (rows return as misses/new on the next run;
  bump `data_generation.enrichment_generation` or wait for the API cache TTL).
- **Backfill order**: 1) migrate, 2) sync/import listens, 3) enrichment can run in parallel (it keeps
  priority on the shared 1 req/s MusicBrainz limiter), 4) run `resolve-artwork --allow-network
  --limit 100` repeatedly (stop on quota/transient output) until the `artwork` counts stop
  growing; artists and albums are the fewest, tracks the most. Do not loop it aggressively.

## Offline rebuild (not upstream reconcile)

Back up first and stop the worker **and every other writer**. Rebuild is a maintenance action,
not an HTTP endpoint; it performs no network calls and does not require a token.

```sh
docker compose stop worker
docker compose run --rm worker music-dashboard rebuild --dry-run
docker compose run --rm worker music-dashboard rebuild
docker compose up -d worker
```

A single transaction re-derives normalized listens from active raw payloads and reprojects
genre/country metadata from the **latest cached** successful/empty lookups. This is not a
restoration of the last applied dashboard state: cache writes can precede projection completion,
so a previously interrupted enrichment may become visible during rebuild. The command supports
one stored user only and refuses unparseable active raw rows rather than deleting their views. Tombstones stay excluded; raw/cache,
quarantine, sync state and queue stay untouched. Both cache generations advance, but ingestion
`last_updated` stays unchanged. Advisory and table locks prevent concurrent mutation; locks
are bounded and busy work fails safely. `--dry-run` rolls back all computed changes.

**Recovery limitation:** transient/pending cache records overwrite the last successful input
in the current cache schema, while live enrichment retains old projections. Raw+cache alone
cannot recover that lost historical input, so rebuild refuses such a cache before mutation.
It is not a universal recovery guarantee for real history. Preserve a verified database backup;
resolve/re-fetch metadata only through the normal authorized real-data process before a later
rebuild. Missing successful cache inputs mean unresolved metadata, never invented genres.

## Checks

```sh
(cd server && .venv/bin/ruff check .)
(cd server && .venv/bin/mypy)
(cd server && .venv/bin/pytest tests/unit -q)
(cd server && .venv/bin/music-dashboard contract --check)
(cd server && .venv/bin/python scripts/check_db.py)
(cd client && npm ci && npm run lint && npm run typecheck && npm test && npm run build && npm run check:api)
```

`scripts/check_db.py` creates an isolated disposable PostgreSQL 17.7 container, initializes the
real role hook, publishes an ephemeral loopback port only for tests, runs all integration
tests, and removes its container even on failure. It does not read .env or use a token.
Tests exercise fresh/two-step upgrades, downgrade/re-upgrade, model/migration parity,
non-superuser writer migration/seed, timezone data, fixture idempotency and role isolation.
API write tests explicitly turn off the read-only session default: table grants still deny
writes. The API timeout test intentionally runs for about five seconds.

For a manually managed **disposable database only**, set TEST_DATABASE_URL (administrator),
TEST_API_DATABASE_URL and TEST_WRITER_DATABASE_URL, then run
`cd server && .venv/bin/pytest tests/integration -q`. These tests DROP SCHEMA public CASCADE;
never point them at an application/production database. Without URLs the tests skip; skips
are not database proof. Unit-only runs do not claim migration verification.

## Regenerate contract and TypeScript

```sh
(cd server && .venv/bin/music-dashboard contract)
(cd client && npm run generate:api)
# Both fail on stale artifacts:
(cd server && .venv/bin/music-dashboard contract --check)
(cd client && npm run check:api)
```

The default contract path assumes cwd=server. An explicit `--output PATH` is available.
Commit models + OpenAPI + generated TS together after integrator approval; do not hand-edit
generated JSON or TS. Public requests use strict YYYY-MM-DD dates (0001-01-01..9999-12-31),
not timestamps; PostgreSQL computes all boundaries. Same-day/full-history requests are valid.

In fixture mode, populated synthetic responses use `from=2026-01-05&to=2026-01-05&tz=UTC&limit=1`.
Rankings also support limit=2 on that range to show missing-title/album states.
Other ranges return empty fixture data in fixture mode. See `server/tests/fixtures/README.md`.
Real mode replaces this adapter with SQL queries; public shapes are unchanged.

## API and client without Compose

```sh
(cd server && .venv/bin/uvicorn music_dashboard.app:app --reload --port 8000)
(cd client && npm run dev)
```

Vite proxies /api to localhost:8000. Fixture API can run without DB or credentials. For local
writer actions set MUSIC_DATABASE_URL privately to a migrated writer database, then
`cd server && .venv/bin/music-dashboard seed-fixtures`. API must never receive that URL.
Without Compose the CLI/worker read `MUSIC_FIXTURE_MODE` from their own process environment
(set it false only for real actions); set it false for the API to use SQL aggregates. Missing
username/token errors belong to the explicit real-data actions, not fixture startup.

## Migrations and extensions

Migrations are **never run at API startup**. Use `alembic upgrade head` as music_writer.
For controlled upgrades: stop worker, back up, apply explicit migration, restart services.
`0001` creates schema/timezone metadata; `0002` grants the approved API tables; `0003` adds `artwork` with column-level API SELECT (`entity_type, entity_mbid, image_url, status`); `0004` adds writer-only provenance columns and `artwork_name` (API column SELECT `entity_type, name_key, image_url, status`). Role creation
and role settings are bootstrap administration, not application migrations. New tables do
not automatically gain public/API grants. Baseline migrations and their timezone snapshot
must remain immutable; a timezone metadata update needs a versioned snapshot and migration,
not rewriting past data. Shared migrations/models remain integrator-owned.

Add a CLI command module exporting configure(subparsers), an API module exporting router,
or a job module exporting tick(); no shared registry edits. See ownership in architecture.md.
The worker discovers job modules. With worker fixture mode true sync ticks are idle
(`fixture-idle`) and enrichment does not run. With `MUSIC_WORKER_FIXTURE_MODE=false`, the worker loop (every 30s) runs
scheduled sync (incremental every 15 min, export reconcile when due/weekly) and enrichment.

## Git workflow

Only permanent branch: main. Each task uses its own worktree and short-lived conventional
branch: `feat/<slug>`, `fix/<slug>`, `refactor/<slug>`, `chore/<slug>`, `docs/<slug>`,
`test/<slug>`. Conventional Commits with scopes, e.g. `feat(server): add listen adapter`.
Workers commit coherent steps and leave a clean tree; coordinator reviews and merges
sequentially. Do not merge, push, rebase others or touch other worktrees without authority.
The approved planning document is external and is not copied into this repository.
