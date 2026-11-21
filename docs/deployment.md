# Local deployment and recovery

No public deployment, DNS or TLS is part of this checkpoint. Web binds loopback by default;
a future existing reverse proxy/TLS must be configured separately after review. Follow
[development](development.md) for private configuration, frozen builds, explicit migrations
and fixture seeding. PostgreSQL is derived from ListenBrainz but backups still contain
private raw payloads and must be protected. Only worker sees the upstream token.

Compose uses the same non-root server image for api and worker, bounded CPU/memory, json-file
rotation (10MB x3), a 5s API DB statement timeout and nginx per-IP API throttling. DB/worker
have no public ports. API database grants are explicit SELECT-only allowlists, not a
read-only setting alone. Bootstrap administrator credentials remain in db only. Worker is a
non-superuser database/schema owner for ingestion and migrations. Migration ownership is a
privileged local action; do not add a public administration endpoint.

## Backup and restore proof (local)

Run against a local seeded stack. Use a new database name for the restore: **never overwrite
an existing database**. The commands below preserve the source database and volume.

```sh
mkdir -p backups
chmod 700 backups
umask 077
docker compose exec -T db pg_dump -U music_writer -Fc music_dashboard > backups/local.dump
docker compose exec -T db createdb -U postgres -O music_writer music_dashboard_restore
docker compose exec -T db pg_restore -U postgres --role=music_writer --no-owner --exit-on-error \\
  -d music_dashboard_restore < backups/local.dump
docker compose exec -T db psql -U music_writer -d music_dashboard_restore \\
  -c 'SELECT count(*) FROM listens'
docker compose exec -T db psql -U music_api -d music_dashboard_restore \\
  -c 'SELECT count(*) FROM listens'
# Expected permission denial; raw payload never becomes API-readable:
docker compose exec -T db psql -U music_api -d music_dashboard_restore \\
  -c 'SELECT count(*) FROM raw_listens'
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

## Deferred operational gates

Authenticated real export smoke, upstream version behavior/completeness, same-second
ambiguity, reconciliation deletion guards, scheduler/backoff, enrichment limiter, real SQL
budgets/cache, Natural Earth map licensing, all charts/browser proof and real-user coverage
are T-003 through T-008, not claims made by foundation fixture startup. Aggregates are
public and can expose listening patterns; the product makes no privacy guarantee.
