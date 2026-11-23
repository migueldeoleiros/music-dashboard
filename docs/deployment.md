# Local deployment and recovery

No public deployment, DNS or TLS is done or automated here. Web binds loopback by default;
an existing reverse proxy/TLS for the eventual `music.miguel.md` must be configured
separately after review (sketch below). Follow
[development](development.md) for private configuration, frozen builds, explicit migrations
and fixture seeding. PostgreSQL is derived from ListenBrainz but backups still contain
private raw payloads and must be protected. Only worker sees the upstream token.

Compose uses the same non-root server image for api and worker, bounded CPU/memory, json-file
rotation (10MB x3), a 5s API DB statement timeout and nginx per-IP API throttling. DB/worker
have no public ports. API database grants are explicit SELECT-only allowlists, not a
read-only setting alone. Bootstrap administrator credentials remain in db only. Worker is a
non-superuser database/schema owner for ingestion and migrations. Migration ownership is a
privileged local action; do not add a public administration endpoint.

## Environment and real-data mode

All configuration lives in ignored `.env` (see `.env.example`). API `MUSIC_FIXTURE_MODE=false`
switches the API to PostgreSQL aggregates (no upstream access). Worker upstream access is
separate: `MUSIC_WORKER_FIXTURE_MODE` defaults true (no upstream calls); set it false
explicitly for real actions and recreate the worker. Worker-only:
`LISTENBRAINZ_USERNAME`, `LISTENBRAINZ_TOKEN`, `MUSIC_METADATA_USER_AGENT` (descriptive contact
User-Agent, required for metadata calls), optional `MUSIC_REFRESH_AUTO_INTERVAL_SECONDS` (3600),
`MUSIC_SYNC_OVERLAP_HOURS` (72), `MUSIC_RECONCILE_INTERVAL_SECONDS` (604800),
`MUSIC_METADATA_{EMPTY,POSITIVE}_TTL_DAYS` (7/30), `MUSIC_METADATA_RETRY_{BASE,MAX}_SECONDS`
(30/86400), `MUSIC_ENRICHMENT_BATCH_SIZE` (100); Compose forwards these to the worker.
First-run commands and
the export-completeness policy (import-complete vs deletion-safe) are in [development](development.md#real-data-first-run).
The API container never receives the token or User-Agent.

Sync reliability notes:

- Upstream `Retry-After` / `X-RateLimit-Reset-In` values must be finite plain decimals (or an
  HTTP date); NaN/inf/negative/exponent forms are ignored. Delays are clamped to 24 h both when
  honoured and when persisted (`sync_runs.stats`). Stored deadlines from older builds (e.g. a
  far-future `next_retry_at`) are bounded on read to 24 h after the run that wrote them, so
  they expire on their own; do not edit rows by hand.
- `MUSIC_EXPORT_MAX_BYTES` (default 67108864 = 64 MiB, allowed 1 MiB..512 MiB) caps the export
  download. The archive is held in memory, so size the worker container's memory limit for
  roughly this value plus parsing overhead before raising it. An oversized export fails
  distinctly (`ExportTooLargeError`), is never retried within the run, and backs the reconcile
  job off for 24 h; it does not block incremental sync.
- The worker is a single serial loop (30 s tick, jobs run one after another, no threads). A long
  export or a rate-limit wait delays every other job, including enrichment and incremental
  sync, until it finishes; there is no per-job timeout beyond HTTP timeouts. Run exactly one
  worker replica (the per-user advisory lock rejects overlap, it does not queue it).

## Eventual reverse proxy (music.miguel.md)

Not deployed or tested here. The `web` container already serves the static client and proxies
same-origin `/api/` to the API; point an existing host proxy at it
(`127.0.0.1:${WEB_PORT}`), terminating TLS there. Illustrative nginx server block:

```nginx
server {
    listen 443 ssl;
    server_name music.miguel.md;
    # ssl_certificate / ssl_certificate_key: managed by your existing TLS setup
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Caveats: the container nginx throttles by `$binary_remote_addr`, which behind a host proxy
is the proxy/gateway address, so all visitors share one 10 r/s bucket; throttle at the host
proxy too or add real-IP handling after review. Only GET statistics and health exist (no admin
routes), but the dashboard is public and aggregates can reveal listening patterns.
Publish only after real-data and browser checks; no DNS/TLS step is performed by this repo.

## Backup and restore proof (local)

Run against a local seeded stack. Use a new database name for the restore: **never overwrite
an existing database**. The commands below preserve the source database and volume.

```sh
mkdir -p backups
chmod 700 backups
umask 077
docker compose exec -T db pg_dump -U music_writer -Fc music_dashboard > backups/local.dump
docker compose exec -T db createdb -U postgres -O music_writer music_dashboard_restore
docker compose exec -T db pg_restore -U postgres --role=music_writer --no-owner --exit-on-error -d music_dashboard_restore < backups/local.dump
# Reapply schema/database boundaries not fully represented in a per-database dump:
docker compose exec -T db psql -U postgres -d music_dashboard_restore -v ON_ERROR_STOP=1 -c \
  'ALTER SCHEMA public OWNER TO music_writer; REVOKE ALL ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO music_api; REVOKE ALL ON DATABASE music_dashboard_restore FROM PUBLIC; GRANT CONNECT ON DATABASE music_dashboard_restore TO music_writer, music_api'
docker compose exec -T db psql -U music_writer -d music_dashboard_restore -c 'SELECT count(*) FROM listens'
docker compose exec -T db psql -U music_api -d music_dashboard_restore -c 'SELECT count(*) FROM listens'
# Expected permission denial; raw payload never becomes API-readable:
docker compose exec -T db psql -U music_api -d music_dashboard_restore -c 'SELECT count(*) FROM raw_listens'
```

Roles must already exist (the local init hook creates them). Preserve ACLs: do not use
--no-acl unless you explicitly reapply the approved grants before bringing an API online.
Database-level CONNECT restrictions are not in a per-database dump: apply them to the new
database as administrator before it is ever used outside this local recovery proof.
For a separate cluster, first bootstrap the private roles, then restore as the owner, verify
grants and only then start the API. Never commit backups or share their payloads/logs.
Off-host backup automation is deferred.

## Upgrade / rollback

Stop worker before applying a controlled migration. Back up first; run
`docker compose run --rm worker alembic upgrade head`, then start worker/API/web. Do not
race migrations through API entrypoints or parallel workers. Downgrade support is tested
on disposable fixture databases, not permission to destroy production tables. Prefer
restoring a verified backup when rollback would discard data. Role initialization hooks do
not rerun on existing volumes; rotate passwords/settings with a private admin session.

## Remaining operational gates

Real imports, incremental sync, and enrichment have run locally. That is not a public deployment or a production load test. ListenBrainz's export has no trusted snapshot manifest, so reconcile imports rows but does not propagate deletions. Initial enrichment can take hours or days and some metadata may never resolve. Public aggregates can reveal listening patterns.
