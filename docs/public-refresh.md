# Opt-in public refresh

Public refresh is **off by default**. This document is an operator plan, not a record of a deployment.
Normal read-only statistics, the existing scheduled sync/reconciliation, and external cron are unchanged.
No public route triggers an export or a rebuild.

## Enable (only during a separately authorized deployment)

1. Back up and apply migration `0005` as the non-superuser `music_writer`, with the worker stopped.
   The migration adds a durable one-row request slot and narrowly scoped admission/status functions.
   `music_api` receives function execution only, **no access to the request table**. Functions pin
   their search path and do not grant execution to `PUBLIC`.
2. Confirm normal scheduled reconciliation has completed for the worker's account. A public request
   waits if the checkpoint requires reconciliation or has no successful initial export. Only the
   normal schedule can perform that prerequisite; do not clear the checkpoint by hand.
3. Set `MUSIC_PUBLIC_REFRESH_ENABLED=true` for **both API and worker** and recreate those services.
   Both fixture flags must retain their intended values. Worker fixture mode must never be disabled
   merely to test the button. The upstream token remains worker-only.
4. Confirm `/api/refresh` reports enabled, same-origin admission works through the public proxy,
   cross-origin POST fails, and the request UUID matches its eventual status. Check HTTPS/Host forwarding
   with the actual TLS terminator before exposing it. Do not trust arbitrary client-supplied forwarded headers.

### Public origin (fail-closed)

Set `MUSIC_PUBLIC_ORIGIN=https://music.miguel.md` on the **API** (compose wires it). When set,
POST admission requires `Origin` to equal that exact HTTPS origin **and** `Host` to be its canonical
host (default port 443 implied). There is no fallback to Host-plus-scheme, `X-Forwarded-*` is never
read, and a malformed value (non-https, path, trailing slash, userinfo, bad port) rejects every POST
with 403. The host TLS proxy must send `proxy_set_header Host $host;` (as in `docs/deployment.md`),
and the container nginx preserves it. Empty keeps the local http behavior.

## Behavior

`GET /api/refresh` reads the shared current/last request. `POST /api/refresh` admits or coalesces a
request; it does not wait for upstream. Both responses are non-cacheable. A deployment-wide durable
cooldown and singleton bound public work across API processes. Repeated requests do not extend the
original deadline. This is a shared public button, not per-visitor authentication or a private queue.
The same-origin check protects browser cross-site submissions; non-browser clients can supply Origin,
so admission's durable cooldown is still essential.

The API admits in a separate transaction whose first SQL statement is `SET TRANSACTION READ WRITE`.
It never changes the connection's default transaction mode. Normal aggregate/status reads stay
read-only. No upstream request is made by the API.

The worker uses the existing account advisory lock and honors persisted retry/rate-limit deadlines.
Public requests perform recent incremental sync only. Blocked requests defer until their fixed expiry.
A crashed worker releases its session lock; another worker can recover the same UUID and safely replay
incremental work. A stale worker's completion cannot complete a newer request. A status deadline bounds
what the UI waits for; it is not cancellation of an upstream call already in progress.

The UI remembers the admitted UUID in session storage, polls active requests, and reloads chart queries
when that UUID succeeds. Page reload resumes observation. If another shared request replaces it, the UI
says so rather than attributing the newer result to the old request. An explicit **Reload charts** action
is also available. Storage failure does not prevent refresh; automatic post-reload correlation then
cannot be guaranteed. A transport failure during admission may have queued work; the UI checks status
before offering retry and never retries POST automatically.

## Disable / rollback

Set `MUSIC_PUBLIC_REFRESH_ENABLED=false` for API **and worker**, then recreate both. The button disappears,
POST returns 404, and public work is no longer claimed. A job already running is not cancelled by changing
an environment variable. To ensure no in-flight public job remains, stop the worker gracefully and inspect
its state before restarting with the flag off. Keep ordinary scheduled sync/cron enabled after rollback.
No database downgrade is needed to disable this feature; retain the harmless singleton and functions.
If a schema rollback is separately authorized, stop refresh-capable API/worker versions first and downgrade
`0005` to `0004`; this drops only public-refresh state/functions, not listening history or scheduled sync state.

## Verification boundaries

Use `server/scripts/check_db.py` for destructive integration checks: it creates/removes its own disposable
PostgreSQL container and initializes the actual API/writer roles. Never set its test URLs to a live database.
Tests must use fake upstream clients; no real token or upstream traffic is needed. Browser fixture evidence
is not proof of production TLS proxy configuration or deployment enable/disable behavior.
