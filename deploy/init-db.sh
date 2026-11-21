#!/bin/sh
# Postgres docker-entrypoint-initdb.d hook. Runs once (empty data dir) as the bootstrap
# superuser POSTGRES_USER=postgres (password POSTGRES_PASSWORD, i.e. MUSIC_ADMIN_PASSWORD).
# Creates:
#   music_writer  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; owns database + schema public
#                 (migrations and the worker run as this role)
#   music_api     LOGIN, read-only, statement timeout; grants come from migration 0002
# Required env: MUSIC_WRITER_PASSWORD, MUSIC_API_PASSWORD (or *_FILE variants).
# Passwords reach psql via \getenv (environment) + stdin: never argv, never logged.
set -eu

for v in MUSIC_WRITER_PASSWORD MUSIC_API_PASSWORD; do
  eval "val=\${$v:-}"
  eval "file=\${${v}_FILE:-}"
  if [ -z "$val" ] && [ -n "$file" ]; then
    eval "$v=\$(cat \"\$file\")"
  fi
  eval "val=\${$v:-}"
  [ -n "$val" ] || { echo "init-db: $v (or ${v}_FILE) is required" >&2; exit 1; }
  export "$v"
done
: "${POSTGRES_DB:?}"
export POSTGRES_DB

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname "$POSTGRES_DB" <<'SQL'
\getenv writer_pw MUSIC_WRITER_PASSWORD
\getenv api_pw MUSIC_API_PASSWORD
\getenv db POSTGRES_DB

SELECT format('CREATE ROLE music_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'writer_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'music_writer') \gexec
SELECT format('ALTER ROLE music_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'writer_pw') \gexec
SELECT format('CREATE ROLE music_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'api_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'music_api') \gexec
SELECT format('ALTER ROLE music_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'api_pw') \gexec

ALTER ROLE music_api SET statement_timeout = '5s';
ALTER ROLE music_api SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE music_api SET default_transaction_read_only = on;

ALTER DATABASE :"db" OWNER TO music_writer;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO music_writer, music_api;
ALTER SCHEMA public OWNER TO music_writer;
SQL
