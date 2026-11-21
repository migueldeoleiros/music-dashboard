# T-008 synthetic integration receipt

Scope: `feat/end-to-end-integration`; no real ListenBrainz/MusicBrainz requests or credentials.
T-007 (actual user history, export completeness and live enrichment) remains open. No deployment,
merge or push. No OpenAPI/generated-type changes or migrations were needed.

## Reproduce

Follow [the isolated SQL-backed synthetic recipe](development.md#larger-synthetic-sql-backed-stack).
Project `md-integ`, loopback port `18150`, API `MUSIC_FIXTURE_MODE=false`, worker fixture flag
true. Migration precedes seeding/startup. The server image is project-scoped to avoid other
worktrees replacing it. `.env` stays ignored and mode 0600; never print expanded Compose config.

`server/scripts/seed_integration.py` feeds real ingestion/cache projection paths with 8,000
listens over 2018–2026. It includes missing IDs/names, collaborations, quarantine and partial
metadata. All data is synthetic. Stop every writer before seeding; the foreign-data guard is
not a concurrency lock. The default cache is replayable positive/empty data; transient cache
scenarios are opt-in and separately covered by tests.

## Defects resolved

- SQL API mode also enabled real worker jobs: separate opt-in worker flag, offline by default.
- Worker metadata User-Agent and scheduling/cache settings were not forwarded by Compose.
- Shared server image tag could collide across worktrees: project-scoped tag.
- Worker startup log falsely claimed no sync/enrichment jobs; log actual discovered modules.
- Enrichment CLI exposed arbitrary exception details; sanitize failures and test the boundary.
- All-time heatmap offered year 0001 and omitted quiet intermediate years; use actual history.
- No offline raw/cache reprojection command: add transactional `rebuild`/`--dry-run`, bounded
  table/advisory locks, monotonic invalidation and preserved ingestion freshness. Refuse
  unparseable active payloads, multiple users and ambiguous transient/pending cache inputs.
- Large synthetic LB-shaped metadata initially used `name` instead of upstream `tag`; correct
  generator and assert human-readable genre names survive the actual normalization adapter.
- Foundation-only docs were stale; add real-data commands, progress, completeness gate,
  offline reprojection limitations and eventual reverse-proxy instructions.

All-time intentionally retains the supported `0001-01-01` bound. No earliest-listen API field
exists; SQL trims extreme evolution bounds to actual history before allocating monthly
buckets, and the heatmap response is sparse. The 8,000-listen all-time range renders 106
monthly evolution buckets, not two millennia of empty buckets. This avoids a contract change.

## Automated evidence

Commands from repository root, freshly run in this worktree:

| Command | Observed result |
| --- | --- |
| `cd server && .venv/bin/mypy` | 58 source files clean |
| `cd server && .venv/bin/pytest tests/unit -q` | 208 passed; one existing AnyIO deprecation warning |
| `cd server && .venv/bin/music-dashboard contract --check` | current |
| `cd server && .venv/bin/python scripts/check_db.py` | 77 passed, including migrations/roles/timezone/rebuild; same warning |
| `cd client && npm run lint && npm run typecheck` | pass |
| `cd client && npm test` | 40 passed across 7 files |
| `cd client && npm run build` | pass; 1.105 MB JS (363.74 kB gzip), chunk-size warning |
| `cd client && npm run check:api` | generated API types current |
| `docker compose -p md-integ config --quiet` | pass |
| `docker compose -p md-integ build` | pass |
| explicit migration, seed, `up -d --wait` | pass; live SQL health via same-origin web proxy |

Full final lint/security/browser refinement receipts are listed below when complete.
The disposable DB checker initializes PostgreSQL 17.7 with the real role hook and removes its
own container. No application database is used for destructive integration tests.
Logs: `/tmp/md-integration-checks/`, `/tmp/md-integration-build.log`,
`/tmp/md-integration-seed.log`, `/tmp/md-integration-seed-repeat.log`.

A standalone server image run with `--network none` and fixture mode true passed health and
all ten chart endpoints with no token or database (`/tmp/md-integration-fixture-proof.log`).
An explicit real `sync` with credentials absent exited 1 with private configuration guidance.
Worker-host tests block sockets, discover/tick sync and enrichment, and verify a failing first
job does not prevent the next or disclose exception text.

The DB regression load check additionally measured 100,030 listens: cold artist ranking
3736.718 ms, genre pie 4403.867 ms, genre evolution 6388.833 ms, heatmap 2933.759 ms,
health 2101.377 ms / warm 2.3 ms. These are single-run test measurements, not an SLA. Total
HTTP requests may execute several individually timeout-bounded SQL statements.

## Browser evidence

Real Chromium 157 via local Playwright, user-authorized fallback from unavailable browser hub;
no mock API. Evidence scripts/reports: `/tmp/md-t008/`. At 1440×1000 and 360×800, all ten
views rendered against SQL and document scroll width equaled viewport width (1440/360),
with no overflow offenders or console/page errors. URL reload, presets, timezone override/reset,
empty range, one-day and all-time were exercised. Screenshots live outside the repository:

- `/tmp/md-integration-shots/desktop-1440x1000-alltime.png`
- `/tmp/md-integration-shots/mobile-360x800-alltime.png`
- `/tmp/md-integration-shots/empty-2010-1440.png`
- `/tmp/md-integration-shots/day-2018-01-01-1440.png`
- `/tmp/md-integration-shots/day-2026-10-31-1440.png`
- `/tmp/md-integration-shots/tooltip-heatmap-retry.png`
- `/tmp/md-integration-shots/tz-tokyo.png`
- `/tmp/md-integration-shots/reload-shared-url.png`

UTC coverage: 2018–2019 genres/countries 0/1500; 2020–2022 genres 1915/2500,
countries 1078/2500; 2026-10-31 both 6/6. All-time: genres 5602/8000, countries
4422/8000. All processed metadata does not imply every listen resolves.

## Recovery and security scope

`server/scripts/verify_stack.py` exercises the running stack and an isolated restored copy;
never point it at production. Detailed results are recorded under `/tmp/md-integration-security/`.
No blanket security acceptance is implied by this file until the final proof results below.

Offline rebuild means reprojection of the **latest stored successful cache**, not restoration
of last-applied metadata: a cache write can precede projection completion. Transient/pending
cache rows may have lost last-good input, so maintenance refuses them. Keep a verified DB
backup; synthetic raw/cache equivalence is not a universal live recovery guarantee.

Literal real-token absence cannot be tested with a real token here (none exists). Build/log/
bundle scans can establish absence of configured DB passwords and credential-shaped values,
and inspect architecture boundaries, but cannot substitute for the later private real run.
The eventual host reverse proxy changes rate-limit source-IP semantics; review before deployment.
Orca workspace metadata updates were unavailable (`runtime_unavailable`); no app was started.
Candidate-id utility was unavailable; these are command/artifact receipts, not fabricated
revision-bound certification.
