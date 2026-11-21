# Music Dashboard

Public, read-only ListenBrainz aggregate dashboard. ListenBrainz remains the source of truth;
PostgreSQL is a rebuildable derived store. No authentication/admin API, raw history endpoint,
scrobble submission or privacy guarantee.

**Status:** full stack in this branch: frozen OpenAPI, migrations and least-privilege roles,
React/ECharts client, PostgreSQL SQL statistics (timezone-aware, cached), LB export
bootstrap/reconcile and incremental sync, and metadata enrichment commands. Synthetic fixture
mode (default) needs no token and never calls upstream. API `MUSIC_FIXTURE_MODE=false`
queries PostgreSQL without upstream credentials. Real worker actions explicitly require
`MUSIC_WORKER_FIXTURE_MODE=false`, private LB username/token for sync, and a metadata User-Agent
for enrichment.

**Not yet proven:** the authenticated LB export has not been smoke-tested against the real
service; the export completeness check relies on an assumed `listen_count` field (see
[development](docs/development.md#real-data-first-run)). Coverage is partial by nature and no
threshold is promised. Public DNS/TLS deployment is not included; a reverse-proxy sketch for
the eventual `music.miguel.md` is in [deployment](docs/deployment.md).

- [Development and local startup](docs/development.md)
- [Architecture, semantics and task ownership](docs/architecture.md)
- [Deployment and backup/restore](docs/deployment.md)
- [Synthetic integration evidence and remaining gates](docs/integration.md)
- Frozen contract: `contracts/openapi.json`; generated client: `client/src/api/schema.d.ts`

Start with the development guide. Synthetic fixture mode never needs a ListenBrainz token.
No public deployment is included. Aggregates are public and can reveal listening patterns.
