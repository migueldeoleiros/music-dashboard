# Unified refresh

One refresh mechanism. Both the public button and the hourly automatic refresh go through the same
database admission function, `public.public_refresh_request()`, and the worker executes the admitted
request (recent incremental sync only; never an export or rebuild). The weekly export reconcile stays a
separate worker job.

## Admission

- `POST /api/refresh` calls `public_refresh_request()` in its own transaction (first statement
  `SET TRANSACTION READ WRITE`; the API role defaults to read-only). The endpoint is open: there is no
  origin or caller check. The **only guard is the cooldown**.
- Policy is fixed in the function (callers cannot choose it): **10 minute cooldown** counted from admission,
  15 minute pending TTL. A request arriving while one is pending/running/deferred coalesces onto it and
  never extends its cooldown or expiry. Otherwise it is rejected as cooling down until `cooldown_until`.
- The worker role `music_writer` has EXECUTE on the same function (migration `0008`). `music_api`
  has function execution only, no access to the request table. Functions are `SECURITY DEFINER`, pin
  `search_path`, and EXECUTE is revoked from `PUBLIC`.
- `GET /api/refresh` reads the shared current/last request (`public_refresh_status()`). Responses are
  non-cacheable. This is a shared button, not per-visitor auth or a private queue. Anyone can trigger one
  sync per 10 minutes (accepted).

## Automatic hourly refresh

The worker keeps an in-process monotonic timer. It fires at startup and then every
`MUSIC_REFRESH_AUTO_INTERVAL_SECONDS` (default 3600), calling the same admission function directly
(no HTTP hop). It admits only if no cooldown is active, so restarts and button presses cannot cause more
than one refresh per cooldown window. If admission errors, the timer is not advanced and the next tick
retries. Logs carry outcome status codes only.

## Execution

Each worker tick admits (if due) and then runs any pending request, serialized with the sync tick by the
existing account advisory lock; persisted retry/rate-limit deadlines are honored. A request defers while the
checkpoint requires reconciliation or no successful initial export exists, until its fixed expiry; the sync
tick's weekly reconcile (promptly when `last_export_at` is NULL) clears that prerequisite. A crashed worker
releases its lock; another worker recovers the same request UUID and safely replays incremental work. A stale
worker's completion cannot complete a newer request.

## Fixture mode

`MUSIC_FIXTURE_MODE=true` (API) reports refresh `enabled=false` and POST returns 404.
`MUSIC_WORKER_FIXTURE_MODE=true` (worker) skips automatic admission and fails any request with `fixture_mode`.
Worker fixture mode must never be disabled merely to test the button. The upstream token remains worker-only.

## UI

The UI remembers the admitted UUID in session storage, polls active requests, and reloads chart queries
when that UUID succeeds. If another shared request replaces it, the UI says so. The cooldown display follows
`cooldown_until`. A transport failure during admission may have queued work; the UI checks status before
offering retry and never retries POST automatically.

## Rollback

Schema: downgrade `0008` to `0007` restores the 300 s function and removes the writer grant (stop the
worker first). To stop automatic refresh without a downgrade, set `MUSIC_REFRESH_AUTO_INTERVAL_SECONDS` very
large or stop the worker; the button remains governed by cooldown.

## Verification boundaries

Use `server/scripts/check_db.py` for destructive integration checks: it creates/removes its own disposable
PostgreSQL container and initializes the actual API/writer roles. Never set its test URLs to a live database.
Tests use fake upstream clients; no real token or upstream traffic is needed.
