# Music Dashboard

Public, read-only ListenBrainz aggregate dashboard. ListenBrainz remains the source of truth;
PostgreSQL is a rebuildable derived store. No authentication/admin API, raw history endpoint,
scrobble submission or privacy guarantee.

**Foundation checkpoint (T-001/T-002):** frozen OpenAPI, synthetic fixtures, database migrations
and least-privilege roles, extensible server ports/jobs/commands, and a React shell. Charts,
real statistics SQL, ingestion and enrichment are intentionally not implemented. Fixture API
responses are explicitly marked, with populated contract examples for one fixed sample range
and empty responses otherwise. Database fixtures are the next agents' shared input, not
fabricated production statistics.

- [Development and local startup](docs/development.md)
- [Architecture, semantics and task ownership](docs/architecture.md)
- [Deployment and backup/restore](docs/deployment.md)
- Frozen contract: `contracts/openapi.json`; generated client: `client/src/api/schema.d.ts`

Start with the development guide. Synthetic fixture mode never needs a ListenBrainz token.
No public deployment is included.
