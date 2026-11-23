# ruff: noqa: E501
"""unify refresh: 10 min cooldown and let the worker role admit requests (hourly auto-refresh)

Replaces public_refresh_request() with v_cool=600 (pending TTL stays 900) and grants EXECUTE to
music_writer so the worker can call the SAME admission function the API uses. Signature,
SECURITY DEFINER and pinned search_path are unchanged; EXECUTE stays revoked from PUBLIC.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

OUT_COLUMNS = """
    request_id uuid, status text, requested_at timestamptz, started_at timestamptz,
    finished_at timestamptz, cooldown_until timestamptz, retry_at timestamptz,
    detail text, coalesced boolean, server_now timestamptz"""

ADMIT_600 = f"""
CREATE OR REPLACE FUNCTION public.public_refresh_request()
RETURNS TABLE ({OUT_COLUMNS})
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_now timestamptz := clock_timestamp();
  v_cool constant integer := 600;
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


ADMIT_300 = f"""
CREATE OR REPLACE FUNCTION public.public_refresh_request()
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


SIG = "public.public_refresh_request()"


def upgrade() -> None:
    op.execute(ADMIT_600)
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIG} TO music_writer")


def downgrade() -> None:
    op.execute(ADMIT_300)
    # Owner keeps its implicit EXECUTE (0005 state); only revoke the explicit grant otherwise.
    op.execute(f"""
    DO $$ BEGIN
      IF (SELECT pg_get_userbyid(proowner) FROM pg_proc
           WHERE oid = '{SIG}'::regprocedure) <> 'music_writer' THEN
        REVOKE EXECUTE ON FUNCTION {SIG} FROM music_writer;
      END IF;
    END $$""")
