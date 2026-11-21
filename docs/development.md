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

No ListenBrainz/MusicBrainz calls are needed or permitted for foundation checks. Never paste,
print or commit a ListenBrainz token. Never run `docker compose config` without `--quiet`
when credentials are configured: expanded environment values can be printed.

## Local Compose (synthetic mode)

Copy `.env.example` to ignored `.env`. Set three **different**, locally generated URL-safe
passwords: MUSIC_ADMIN_PASSWORD, MUSIC_WRITER_PASSWORD, MUSIC_API_PASSWORD. Keep the file
mode 0600. Leave LISTENBRAINZ_USERNAME/TOKEN empty and MUSIC_FIXTURE_MODE=true.
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
avoid the temporary init socket server. Foundation API health is synthetic liveness and
explicitly `mode=fixture`, not a database readiness check or a successful real sync report.
Fixture examples are contract demonstrations, not statistics computed from the seeded DB.

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

Populated synthetic responses use `from=2026-01-05&to=2026-01-05&tz=UTC&limit=1`.
Other ranges currently return empty fixture data. See `server/tests/fixtures/README.md`.
The later statistics agent replaces this adapter, not the frozen public shapes.

## API and client without Compose

```sh
(cd server && .venv/bin/uvicorn music_dashboard.app:app --reload --port 8000)
(cd client && npm run dev)
```

Vite proxies /api to localhost:8000. Fixture API can run without DB or credentials. For local
writer actions set MUSIC_DATABASE_URL privately to a migrated writer database, then
`cd server && .venv/bin/music-dashboard seed-fixtures`. API must never receive that URL.
The real-data actions are intentionally absent until T-003/T-004; missing token errors
belong to those explicit actions, not fixture startup.

## Migrations and extensions

Migrations are **never run at API startup**. Use `alembic upgrade head` as music_writer.
For controlled upgrades: stop worker, back up, apply explicit migration, restart services.
`0001` creates schema/timezone metadata; `0002` grants the approved API tables. Role creation
and role settings are bootstrap administration, not application migrations. New tables do
not automatically gain public/API grants. Baseline migrations and their timezone snapshot
must remain immutable; a timezone metadata update needs a versioned snapshot and migration,
not rewriting past data. Shared migrations/models remain integrator-owned.

Add a CLI command module exporting configure(subparsers), an API module exporting router,
or a job module exporting tick(); no shared registry edits. See ownership in architecture.md.
Worker is currently an idle, extensible host, not a functioning sync implementation.

## Git workflow

Only permanent branch: main. Each task uses its own worktree and short-lived conventional
branch: `feat/<slug>`, `fix/<slug>`, `refactor/<slug>`, `chore/<slug>`, `docs/<slug>`,
`test/<slug>`. Conventional Commits with scopes, e.g. `feat(server): add listen adapter`.
Workers commit coherent steps and leave a clean tree; coordinator reviews and merges
sequentially. Do not merge, push, rebase others or touch other worktrees without authority.
The approved planning document is external and is not copied into this repository.
