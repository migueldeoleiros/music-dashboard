# ruff: noqa: E501
"""public refresh: durable singleton request row + narrow SECURITY DEFINER API surface

`refresh_state` is a one-row table (CHECK singleton) holding the current/last public refresh
request. The API role gets NO table grants; it may only EXECUTE two functions:

* public_refresh_request(): admission with a FIXED policy (5 min cooldown, 15 min pending TTL;
  the API role can execute it, so callers cannot choose or shorten either). Locks the singleton row, lazily
  expires stale requests, coalesces onto an in-flight request (never extends cooldown or
  expiry) and otherwise starts a new request unless the cooldown is still active.
* public_refresh_status(): read-only (STABLE) status with virtual expiry.

Both are SECURITY DEFINER with a pinned search_path (pg_catalog, pg_temp) and fully
schema-qualified relations; EXECUTE is revoked from PUBLIC. The admission function writes, so
the API must call it in its own transaction starting with SET TRANSACTION READ WRITE (the API
role defaults to read-only).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

OUT_COLUMNS = """
    request_id uuid, status text, requested_at timestamptz, started_at timestamptz,
    finished_at timestamptz, cooldown_until timestamptz, retry_at timestamptz,
    detail text, coalesced boolean, server_now timestamptz"""

ADMIT = f"""
CREATE FUNCTION public.public_refresh_request()
RETURNS TABLE ({OUT_COLUMNS})
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_now timestamptz := clock_timestamp();
  v_cool constant integer := 300;
  v_pend constant integer := 900;
  v_row public.refresh_state%ROWTYPE;
  v_coalesced boolean := false;
BEGIN
  SELECT * INTO v_row FROM public.refresh_state AS s WHERE s.singleton FOR UPDATE;
  IF v_row.status IN ('pending', 'running', 'deferred') AND v_row.expires_at <= v_now THEN
    UPDATE public.refresh_state AS s
       SET status = 'expired', finished_at = v_now, detail = 'expired'
     WHERE s.singleton
     RETURNING * INTO v_row;
  END IF;
  IF v_row.status IN ('pending', 'running', 'deferred') THEN
    v_coalesced := true;  -- join in-flight request; cooldown and expiry untouched
  ELSIF v_row.cooldown_until IS NULL OR v_row.cooldown_until <= v_now THEN
    UPDATE public.refresh_state AS s
       SET request_id = gen_random_uuid(), status = 'pending', requested_at = v_now,
           started_at = NULL, finished_at = NULL, retry_at = NULL, detail = NULL,
           expires_at = v_now + make_interval(secs => v_pend),
           cooldown_until = v_now + make_interval(secs => v_cool)
     WHERE s.singleton
     RETURNING * INTO v_row;
  END IF;
  RETURN QUERY SELECT v_row.request_id, v_row.status, v_row.requested_at, v_row.started_at,
    v_row.finished_at, v_row.cooldown_until, v_row.retry_at, v_row.detail, v_coalesced, v_now;
END $$"""

STATUS = f"""
CREATE FUNCTION public.public_refresh_status()
RETURNS TABLE ({OUT_COLUMNS})
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  SELECT s.request_id,
         CASE WHEN s.status IN ('pending', 'running', 'deferred')
                   AND s.expires_at <= clock_timestamp() THEN 'expired' ELSE s.status END,
         s.requested_at, s.started_at,
         CASE WHEN s.status IN ('pending', 'running', 'deferred')
                   AND s.expires_at <= clock_timestamp() THEN s.expires_at ELSE s.finished_at END,
         s.cooldown_until, s.retry_at,
         CASE WHEN s.status IN ('pending', 'running', 'deferred')
                   AND s.expires_at <= clock_timestamp() THEN 'expired' ELSE s.detail END,
         false, clock_timestamp()
    FROM public.refresh_state AS s WHERE s.singleton
$$"""


def upgrade() -> None:
    op.execute("""
    CREATE TABLE public.refresh_state (
      singleton boolean NOT NULL DEFAULT true,
      CONSTRAINT pk_refresh_state PRIMARY KEY (singleton),
      CONSTRAINT ck_refresh_state_singleton CHECK (singleton),
      request_id uuid,
      status text NOT NULL DEFAULT 'idle' CONSTRAINT ck_refresh_state_status CHECK (
        status IN ('idle','pending','running','succeeded','failed','expired','deferred')),
      requested_at timestamptz,
      started_at timestamptz,
      finished_at timestamptz,
      expires_at timestamptz,
      cooldown_until timestamptz,
      retry_at timestamptz,
      detail text CONSTRAINT ck_refresh_state_detail CHECK (detail ~ '^[a-z_]{1,64}$'),
      CONSTRAINT ck_refresh_state_inflight CHECK (
        status NOT IN ('pending','running','deferred') OR (request_id IS NOT NULL AND expires_at IS NOT NULL))
    )""")
    op.execute("INSERT INTO public.refresh_state (singleton) VALUES (true)")
    op.execute("REVOKE ALL ON public.refresh_state FROM PUBLIC, music_api")
    op.execute(ADMIT)
    op.execute(STATUS)
    for sig in ("public_refresh_request()", "public_refresh_status()"):
        op.execute(f"REVOKE ALL ON FUNCTION public.{sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{sig} TO music_api")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.refresh_state TO music_writer")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.public_refresh_status()")
    op.execute("DROP FUNCTION public.public_refresh_request()")
    op.execute("DROP TABLE public.refresh_state")
