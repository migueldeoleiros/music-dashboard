"""Reusable integration/security proof for a RUNNING Compose stack (default project md-integ).

Host-side, stdlib only. Containers are found by compose labels and driven with `docker exec`, so
no compose file/.env is parsed. Secrets are read from container environments into memory only to
scan for leakage; they are never printed or written (all output is redacted). The script never
starts/stops the stack, never touches the working database destructively, and makes no upstream
calls. It creates and drops only the temporary database `md_integ_restore` (use --keep-restore).

Usage: python server/scripts/verify_stack.py [--base-url http://127.0.0.1:18150]
       [--project md-integ] [--out /tmp/md-integration-security] [--tz UTC --tz Europe/Madrid]
       [--from YYYY-MM-DD --to YYYY-MM-DD] [--burst] [--keep-restore]
Exit code 1 if any check FAILs. Statuses: PASS FAIL WARN INFO SKIP.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
API_TABLES = {
    "listens",
    "listen_artists",
    "genres",
    "entity_genres",
    "artist_countries",
    "data_generation",
    "timezones",
}
FORBIDDEN_TABLES = {
    "raw_listens",
    "quarantined_listens",
    "sync_runs",
    "sync_state",
    "metadata_cache",
    "enrichment_queue",
    "alembic_version",
}
CHARTS = [
    "rankings/artists",
    "rankings/albums",
    "rankings/tracks",
    "rankings/genres",
    "heatmap",
    "countries",
    "evolution/artists",
    "evolution/genres",
    "pies/artists",
    "pies/genres",
]
ALLOWED_IMAGE_ENV = {
    "PATH",
    "LANG",
    "GPG_KEY",
    "PYTHON_VERSION",
    "PYTHON_SHA256",
    "PYTHONUNBUFFERED",
    "NGINX_VERSION",
    "NJS_VERSION",
    "NJS_RELEASE",
    "PKG_RELEASE",
    "DYNPKG_RELEASE",
    "NODE_VERSION",
    "YARN_VERSION",
    "PG_MAJOR",
    "PG_VERSION",
    "PGDATA",
    "GOSU_VERSION",
    "PG_SHA256",
    "DOCKER_PG_LLVM_DEPS",  # upstream postgres image build metadata
}
SECRET_NAME = re.compile(r"PASSWORD|TOKEN|SECRET|CREDENTIAL|API_?KEY|PRIVATE", re.I)
CRED_PATTERNS = {
    "url_with_password": re.compile(r"[a-z+]+://[^/\s:@]+:[^@\s/]{3,}@"),
    "token_assignment_nonempty": re.compile(r"LISTENBRAINZ_TOKEN\s*[=:]\s*['\"]?[A-Za-z0-9-]{8,}"),
    "authorization_token": re.compile(r"Authorization:\s*Token\s+\S{8,}", re.I),
    "password_assignment_nonempty": re.compile(r"MUSIC_\w*PASSWORD\s*[=:]\s*['\"]?\S{6,}"),
}

SECRETS: list[str] = []
RESULTS: list[dict[str, Any]] = []


def redact(text: str) -> str:
    for s in SECRETS:
        text = text.replace(s, "[REDACTED]")
    return text


def record(cid: str, status: str, summary: str, **evidence: Any) -> None:
    ev = json.loads(redact(json.dumps(evidence, default=str)))
    RESULTS.append({"id": cid, "status": status, "summary": redact(summary), "evidence": ev})
    print(f"[{status:4}] {cid}: {redact(summary)}", flush=True)


def run(args: list[str], stdin: str | None = None, timeout: int = 300) -> tuple[int, str, str]:
    p = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def container(project: str, service: str) -> str:
    _, out, _ = run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ]
    )
    ids = out.split()
    if not ids:
        raise SystemExit(f"service {service} not running in project {project}")
    return ids[0]


def psql(db_c: str, db: str, sql: str) -> str:
    code, out, err = run(
        [
            "docker",
            "exec",
            "-i",
            db_c,
            "psql",
            "-U",
            "postgres",
            "-d",
            db,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-F",
            "|",
            "-c",
            sql,
        ]
    )
    if code:
        raise RuntimeError(redact(err.strip()[-400:]))
    return out.strip()


# Executed INSIDE api/worker containers (stdin). Receives one JSON argument. Prints one JSON line.
HELPER = r"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

from sqlalchemy.engine import make_url

A = json.loads(sys.argv[1])
mode = A["mode"]


def url(db=None, driver=None):
    u = make_url(os.environ["MUSIC_DATABASE_URL"])
    if db:
        u = u.set(database=db)
    if driver:
        u = u.set(drivername=driver)
    return u.render_as_string(hide_password=False)


def out(o):
    print(json.dumps(o, default=str))


STRIP = {"generation", "last_updated", "sync_lag_seconds"}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in STRIP}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def canon(o):
    return json.dumps(
        o, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    ).encode()


def h(b):
    return hashlib.sha256(b).hexdigest()


def drange(db):
    import psycopg

    with psycopg.connect(url(db, "postgresql")) as c:
        r = c.execute(
            "SELECT min((listened_at AT TIME ZONE 'UTC')::date),"
            " max((listened_at AT TIME ZONE 'UTC')::date), count(*) FROM listens"
        ).fetchone()
    return r


if mode == "roles":
    import psycopg

    res = {}

    def attempt(label, sql, ro_off=True):
        c = psycopg.connect(url(None, "postgresql"), autocommit=True)
        try:
            if ro_off:
                c.execute("SET default_transaction_read_only = off")
            c.execute("BEGIN")  # explicit txn: always rolled back, even if a statement were allowed
            ro = c.execute("SHOW transaction_read_only").fetchone()[0]
            try:
                c.execute(sql)
                r = "ALLOWED"
            except psycopg.Error as e:
                r = e.sqlstate or type(e).__name__
            res[label] = {"result": r, "transaction_read_only_during_attempt": ro}
        finally:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            c.close()

    c0 = psycopg.connect(url(None, "postgresql"))
    me = c0.execute(
        "SELECT current_user,"
        " (SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles"
        "  WHERE rolname=current_user),"
        " current_setting('statement_timeout'),"
        " current_setting('default_transaction_read_only'),"
        " has_schema_privilege('public','CREATE'),"
        " has_database_privilege(current_database(),'CREATE')"
    ).fetchone()
    c0.close()
    for t in A["approved"]:
        attempt("select:" + t, f"SELECT 1 FROM public.{t} LIMIT 1", False)
        c = psycopg.connect(url(None, "postgresql"))
        col = c.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass"
            " AND attnum>0 AND NOT attisdropped LIMIT 1",
            ("public." + t,),
        ).fetchone()[0]
        c.close()
        attempt("insert:" + t, f"INSERT INTO public.{t} SELECT * FROM public.{t} WHERE false")
        attempt("update:" + t, f'UPDATE public.{t} SET "{col}" = "{col}" WHERE false')
        attempt("delete:" + t, f"DELETE FROM public.{t} WHERE false")
        attempt("truncate:" + t, f"TRUNCATE public.{t}")
    for t in A["forbidden"]:
        attempt("select:" + t, f"SELECT 1 FROM public.{t} LIMIT 1", False)
        attempt("select_ro_off:" + t, f"SELECT 1 FROM public.{t} LIMIT 1")
        attempt("insert:" + t, f"INSERT INTO public.{t} SELECT * FROM public.{t} WHERE false")
    attempt("ddl:create_table", "CREATE TABLE public.md_integ_probe(i int)")
    attempt("ddl:drop_listens", "DROP TABLE public.listens")
    attempt("ddl:alter_listens", "ALTER TABLE public.listens ADD COLUMN md_probe int")
    attempt("fn:pg_read_file", "SELECT pg_read_file('/etc/passwd')")
    out({"me": me, "attempts": res})

elif mode == "timeout":
    import psycopg

    c = psycopg.connect(url(None, "postgresql"))
    st = c.execute("SHOW statement_timeout").fetchone()[0]
    t0 = time.monotonic()
    try:
        c.execute("SELECT pg_sleep(9)")
        r = "NOT_CANCELLED"
    except psycopg.Error as e:
        r = e.sqlstate
    out({"statement_timeout": st, "sleep9_result": r, "elapsed_s": round(time.monotonic() - t0, 2)})

elif mode == "routes":
    import music_dashboard
    from music_dashboard.app import app

    rows = sorted({(r.path, tuple(sorted(r.methods or []))) for r in app.routes})
    out(
        {
            "routes": rows,
            "module_file": music_dashboard.__file__,
            "fixture_mode_env": os.environ.get("MUSIC_FIXTURE_MODE"),
        }
    )

elif mode == "srchash":
    base = "/app/server/src"
    d = {}
    for dp, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if x != "__pycache__"]
        for f in fn:
            if f.endswith((".py", ".json")):
                p = os.path.join(dp, f)
                d[os.path.relpath(p, base)] = h(open(p, "rb").read())
    out(d)

elif mode == "cache":
    from sqlalchemy import create_engine

    from music_dashboard.domain.statistics import policies as P
    from music_dashboard.domain.statistics.models import StatisticsFilter
    from music_dashboard.infrastructure.persistence.statistics_queries import (
        ResponseCache,
        SQLStatisticsQueries,
    )

    lo, hi, n = drange(A.get("db"))
    eng = create_engine(url(A.get("db")))
    cache = ResponseCache(ttl=60, max_entries=4, max_bytes=P.CACHE_MAX_BYTES)
    q = SQLStatisticsQueries(eng, cache)
    for lim in range(1, 13):
        q.query("rankings/artists", StatisticsFilter(lo, hi, "UTC", lim))
    tiny = ResponseCache(ttl=60, max_entries=128, max_bytes=2000)
    q2 = SQLStatisticsQueries(eng, tiny)
    for lim in range(1, 13):
        q2.query("rankings/tracks", StatisticsFilter(lo, hi, "UTC", lim))
    q.query("rankings/artists", StatisticsFilter(lo, hi, "UTC", 12))
    eng.dispose()
    out(
        {
            "configured": {
                "ttl": P.CACHE_TTL_SECONDS,
                "max_entries": P.CACHE_MAX_ENTRIES,
                "max_bytes": P.CACHE_MAX_BYTES,
            },
            "entries_after_12_distinct_with_cap4": len(cache.entries),
            "cap": 4,
            "tiny_cache_bytes": tiny.bytes,
            "tiny_cap_bytes": 2000,
            "tiny_entries": len(tiny.entries),
        }
    )

elif mode == "charts":
    from sqlalchemy import create_engine

    from music_dashboard.domain.statistics.models import StatisticsFilter
    from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

    lo, hi, n = drange(A.get("db"))
    if n == 0:
        out({"error": "no listens in database"})
        sys.exit(0)
    f = date.fromisoformat(A["from"]) if A.get("from") else lo - timedelta(days=1)
    t = date.fromisoformat(A["to"]) if A.get("to") else hi + timedelta(days=1)
    eng = create_engine(url(A.get("db")), pool_pre_ping=True)
    q = SQLStatisticsQueries(eng)
    res = {}
    from music_dashboard.infrastructure.persistence.timezones import load_timezones

    zones = load_timezones()
    for tz in A["tzs"]:
        tz = zones[tz]
        for fam in A["families"]:
            r = q.query(fam, StatisticsFilter(f, t, tz, 10))
            res[f"{fam}|{tz}"] = {
                "full": h(canon(r)),
                "stripped": h(canon(strip(r))),
                "bytes": len(canon(r)),
            }
            if A.get("json"):
                res[f"{fam}|{tz}"]["json"] = canon(strip(r)).decode()
    http = {}
    if A.get("http"):
        for tz in A["tzs"][:1]:
            for fam in A["families"]:
                u = f"http://127.0.0.1:8000/api/{fam}?from={f}&to={t}&tz={tz}&limit=10"
                body = json.loads(urllib.request.urlopen(u, timeout=30).read())
                http[f"{fam}|{tz}"] = h(canon(body))
    eng.dispose()
    out({"range": [str(f), str(t)], "listens": n, "charts": res, "http_canon": http})

elif mode == "load":
    f = date.fromisoformat(A["from"])
    t = date.fromisoformat(A["to"])
    tz = A["tz"]

    def req(fam, k=0, lim=10):
        to = t + timedelta(days=k)
        u = f"http://127.0.0.1:8000/api/{fam}?from={f}&to={to}&tz={tz}&limit={lim}"
        t0 = time.perf_counter()
        try:
            code = urllib.request.urlopen(u, timeout=60).status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = -1
        return (time.perf_counter() - t0) * 1000, code

    def stats(rs):
        ms = sorted(x for x, _ in rs)
        n = len(ms)
        return {
            "n": n,
            "min_ms": round(ms[0], 1),
            "p50_ms": round(ms[n // 2], 1),
            "p95_ms": round(ms[min(n - 1, int(n * 0.95))], 1),
            "max_ms": round(ms[-1], 1),
            "mean_ms": round(sum(ms) / n, 1),
            "non200": sum(1 for _, c in rs if c != 200),
        }

    base = (
        int(time.time()) % 3000 + 400
    )  # unique cache keys per run (to-date shifted, may exceed data range)
    fams = A["families"]
    per = {}
    cold = []
    warm = []
    for i, fam in enumerate(fams):
        c = req(fam, base + i)
        cold.append(c)
        w = [req(fam, base + i) for _ in range(10)]
        warm += w
        per[fam] = {
            "cold_ms": round(c[0], 1),
            "warm_p50_ms": round(sorted(x for x, _ in w)[5], 1),
            "status": c[1],
        }
    from concurrent.futures import ThreadPoolExecutor

    N = A["concurrency"]
    with ThreadPoolExecutor(N) as ex:
        cc = list(
            ex.map(lambda j: req(fams[j % len(fams)], base + 100 + j // len(fams)), range(N * 10))
        )
        t0 = time.perf_counter()
        cw = list(ex.map(lambda j: req(fams[j % len(fams)], base + j % len(fams)), range(N * 10)))
        wall = time.perf_counter() - t0
    out(
        {
            "transport": "in-container loopback to api:8000 (bypasses nginx rate limit)",
            "range": [str(f), str(t)],
            "tz": tz,
            "cold_sequential": stats(cold),
            "warm_sequential": stats(warm),
            f"concurrent_{N}_mostly_cold": stats(cc),
            f"concurrent_{N}_warm": stats(cw),
            "concurrent_warm_wall_s": round(wall, 2),
            "concurrent_warm_rps": round(len(cw) / wall, 1),
            "per_chart": per,
        }
    )

elif mode == "rawhttp":
    import socket

    port = None
    for cand in range(18990, 19000):
        with socket.socket() as so:
            if so.connect_ex(("127.0.0.1", cand)) != 0:
                port = cand
                break
    if port is None:
        out({"error": "no free loopback port in 18990-18999"})
        sys.exit(0)
    env = dict(os.environ, MUSIC_DATABASE_URL=url(A["db"]), MUSIC_FIXTURE_MODE="false")
    pr = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "music_dashboard.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        cwd="/app/server",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            out({"error": "temporary uvicorn did not become ready"})
            sys.exit(0)

        def raw(base, path):
            try:
                r = urllib.request.urlopen(base + path, timeout=60)
                return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        res = {}
        paths = ["/api/health"] + [
            f"/api/{fam}?from={A['from']}&to={A['to']}&tz={tz}&limit=10"
            for tz in A["tzs"]
            for fam in A["families"]
        ]
        for pth in paths:
            a = raw("http://127.0.0.1:8000", pth)
            b = raw(f"http://127.0.0.1:{port}", pth)
            res[
                pth.split("?")[0]
                + ("|" + pth.split("tz=")[1].split("&")[0] if "tz=" in pth else "")
            ] = {
                "live_status": a[0],
                "restored_status": b[0],
                "live_sha": h(a[1]),
                "restored_sha": h(b[1]),
                "bytes": len(a[1]),
            }
        out({"port": port, "responses": res})
    finally:
        pr.terminate()
        try:
            pr.wait(10)
        except Exception:
            pr.kill()

elif mode == "cli":
    env = dict(os.environ, MUSIC_DATABASE_URL=url(A["db"]), MUSIC_FIXTURE_MODE="true")
    env.pop("LISTENBRAINZ_TOKEN", None)  # offline: no upstream credential reaches the child
    code, so, se = 0, "", ""
    p = subprocess.run(
        A["argv"], env=env, cwd="/app/server", capture_output=True, text=True, timeout=600
    )
    out(
        {"returncode": p.returncode, "stdout_tail": p.stdout[-600:], "stderr_tail": p.stderr[-600:]}
    )
"""


def helper(c: str, user: str | None, **args: Any) -> dict[str, Any]:
    cmd = ["docker", "exec", "-i", c, "python", "-", json.dumps(args)]
    code, out, err = run(cmd, stdin=HELPER, timeout=900)
    if code:
        raise RuntimeError(f"helper {args.get('mode')} failed: {redact(err.strip()[-500:])}")
    return json.loads(out.strip().splitlines()[-1])  # type: ignore[no-any-return]


def http(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, data=b"{}" if method != "GET" else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return -1, b""


def getenv(c: str, name: str) -> str:
    return run(["docker", "exec", c, "printenv", name])[1].rstrip("\n")


def check_source(api: str, worker: str) -> None:
    host: dict[str, str] = {}
    base = ROOT / "server" / "src"
    for p in base.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts:
            host[str(p.relative_to(base))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for name, c in (("api", api), ("worker", worker)):
        got = helper(c, None, mode="srchash")
        diff = sorted(k for k in set(host) | set(got) if host.get(k) != got.get(k))
        record(
            f"source.current.{name}",
            "PASS" if not diff else "FAIL",
            f"container /app/server/src matches host worktree ({len(host)} files)"
            if not diff
            else f"{len(diff)} files differ: image stale vs worktree",
            differing=diff[:20],
        )


def check_roles(db: str, api: str) -> None:
    rows = psql(
        db,
        "music_dashboard",
        "SELECT c.relname, has_table_privilege('music_api',c.oid,'SELECT'),"
        " has_table_privilege('music_api',c.oid,"
        "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),"
        " c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace"
        " WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') ORDER BY 1",
    )
    sel, wr, tabs = set(), [], set()
    for line in rows.splitlines():
        name, s, w, kind = line.split("|")
        if kind == "S":
            if w == "t" or s == "t":
                wr.append(name)
            continue
        tabs.add(name)
        if s == "t":
            sel.add(name)
        if w == "t":
            wr.append(name)
    ok = sel == API_TABLES and not wr and FORBIDDEN_TABLES <= tabs
    record(
        "role.catalog.select_only",
        "PASS" if ok else "FAIL",
        "music_api has SELECT on exactly the 7 approved tables, no "
        "write/ref/trigger/sequence rights",
        select_tables=sorted(sel),
        unexpected_select=sorted(sel - API_TABLES),
        missing_select=sorted(API_TABLES - sel),
        write_privileged=wr,
        forbidden_tables_present=sorted(FORBIDDEN_TABLES & tabs),
    )
    meta = psql(
        db,
        "music_dashboard",
        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
        "coalesce(array_to_string(rolconfig,';'),'') FROM pg_roles WHERE rolname='music_api'",
    )
    s, cdb, crole, rep, bypass, cfg = meta.split("|")
    ok = (
        (s, cdb, crole, rep, bypass) == ("f",) * 5
        and "statement_timeout=5s" in cfg
        and ("default_transaction_read_only=on" in cfg)
    )
    record(
        "role.attributes",
        "PASS" if ok else "FAIL",
        "music_api not superuser/createdb/createrole/replication/bypassrls; role config sets "
        "statement_timeout=5s + default_transaction_read_only=on",
        role_config=cfg,
    )
    r = helper(
        api, None, mode="roles", approved=sorted(API_TABLES), forbidden=sorted(FORBIDDEN_TABLES)
    )
    at = r["attempts"]
    bad = [
        k
        for k, v in at.items()
        if (k.startswith("select:") and k[7:] in API_TABLES and v["result"] != "ALLOWED")
        or (k.startswith("select:") and k[7:] in FORBIDDEN_TABLES and v["result"] != "42501")
        or (not k.startswith("select:") and v["result"] != "42501")
    ]
    not_off = [
        k
        for k, v in at.items()
        if not k.startswith("select:") and v["transaction_read_only_during_attempt"] != "off"
    ]
    record(
        "role.runtime.writes_denied_read_only_off",
        "PASS" if not bad and not not_off else "FAIL",
        f"{len(at)} runtime attempts as music_api (session read_only forced off): SELECT "
        f"approved ok; "
        "raw/sync/cache SELECT, all writes, TRUNCATE, DDL, pg_read_file → 42501",
        unexpected=bad,
        read_only_not_off=not_off,
        sqlstates={k: v["result"] for k, v in at.items()},
        me=r["me"],
    )
    t = helper(api, None, mode="timeout")
    ok = (
        t["statement_timeout"] == "5s"
        and t["sleep9_result"] == "57014"
        and 4 <= t["elapsed_s"] <= 7
    )
    record(
        "db.statement_timeout",
        "PASS" if ok else "FAIL",
        "pg_sleep(9) as music_api cancelled by statement_timeout (57014) at ~5s",
        **t,
    )


def check_routes(api: str, base: str) -> None:
    r = helper(api, None, mode="routes")
    routes = [(p, m) for p, m in r["routes"]]
    want = {f"/api/{c}" for c in CHARTS} | {"/api/health"}
    app_paths = {p for p, _ in routes if p.startswith("/api/")}
    bad_methods = [(p, m) for p, m in routes if set(m) - {"GET", "HEAD"}]
    record(
        "routes.inventory",
        "PASS" if app_paths == want and not bad_methods else "FAIL",
        f"{len(app_paths)} /api routes, all GET/HEAD only; no admin/sync/write routes",
        api_paths=sorted(app_paths),
        non_api_routes=[p for p, _ in routes if not p.startswith("/api/")],
        unexpected_paths=sorted(app_paths - want),
        missing=sorted(want - app_paths),
        non_get=bad_methods,
        module_file=r["module_file"],
    )
    blocked = {}
    for path in ("/api/health", f"/api/{CHARTS[0]}", "/api/admin", "/api/sync"):
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            blocked[f"{m} {path}"] = http(base + path, m)[0]
    ok = all(c in (404, 405) for c in blocked.values())
    record(
        "routes.http_writes_rejected",
        "PASS" if ok else "FAIL",
        "mutating verbs via proxy return 404/405",
        statuses=blocked,
    )
    time.sleep(2)  # let the proxy rate limiter refill after probes
    st, body = http(base + "/api/health")
    j = json.loads(body) if st == 200 else {}
    live = j.get("mode") == "live"
    record(
        "api.mode_live_sql",
        "PASS" if live else "FAIL",
        "API serves SQL adapter (MUSIC_FIXTURE_MODE=false)"
        if live
        else "API health mode is not 'live'; it is serving the synthetic fixture adapter, "
        "not the DB",
        http_status=st,
        mode=j.get("mode"),
        health_status=j.get("status"),
        generation=j.get("generation"),
        sync_lag_seconds=j.get("sync_lag_seconds"),
        coverage=j.get("coverage"),
    )


def check_burst(base: str) -> None:
    time.sleep(3)

    def one(_: int) -> int:
        return http(base + "/api/health")[0]

    with concurrent.futures.ThreadPoolExecutor(40) as ex:
        codes = list(ex.map(one, range(80)))
    tally = {str(c): codes.count(c) for c in sorted(set(codes))}
    ok = codes.count(429) > 0 and codes.count(200) > 0
    record(
        "proxy.burst_429",
        "PASS" if ok else "FAIL",
        "80 concurrent GET /api/health via nginx (limit 10r/s burst 20): some 200, some 429",
        tally=tally,
    )
    time.sleep(3)


def check_cache(api: str, db: str) -> None:
    r = helper(api, None, mode="cache", db=None)
    ok = (
        r["entries_after_12_distinct_with_cap4"] <= 4
        and r["tiny_cache_bytes"] <= r["tiny_cap_bytes"]
    )
    record(
        "cache.bounded_runtime",
        "PASS" if ok else "FAIL",
        "live SQLStatisticsQueries with ResponseCache(max_entries=4,max_bytes=2000) stays "
        "within caps "
        "on 12 distinct keys",
        **r,
        prior_test_evidence="server/tests/unit/test_statistics.py::"
        "test_cache_ttl_lru_bytes_and_copy_isolation "
        "(TTL, LRU, byte budget, copy isolation; not re-run here)",
    )


def check_ports_resources(project: str, cs: dict[str, str]) -> None:
    ports, res = {}, {}
    for svc, c in cs.items():
        code, out, _ = run(["docker", "inspect", c])
        i = json.loads(out)[0]
        pb = {k: v for k, v in (i["HostConfig"].get("PortBindings") or {}).items() if v}
        live = {k: v for k, v in (i["NetworkSettings"].get("Ports") or {}).items() if v}
        ports[svc] = {
            "published": live,
            "declared_exposed": sorted(i["Config"].get("ExposedPorts") or {}),
        }
        hc = i["HostConfig"]
        res[svc] = {
            "mem_bytes": hc["Memory"],
            "nano_cpus": hc["NanoCpus"],
            "security_opt": hc["SecurityOpt"],
            "restart": hc["RestartPolicy"]["Name"],
            "log_driver": hc["LogConfig"]["Type"],
            "log_config": hc["LogConfig"]["Config"],
            "pb": pb,
        }
    nopub = all(not ports[s]["published"] for s in ("db", "api", "worker"))
    web = ports["web"]["published"]
    web_ok = bool(web) and all(b["HostIp"] == "127.0.0.1" for v in web.values() for b in v)
    record(
        "ports.db_api_worker_unpublished",
        "PASS" if nopub and web_ok else "FAIL",
        "db/api/worker publish no host ports; web only on 127.0.0.1",
        ports=ports,
    )
    bad = [
        s
        for s, r in res.items()
        if not (
            r["mem_bytes"]
            and r["nano_cpus"]
            and r["log_driver"] == "json-file"
            and r["log_config"].get("max-size")
            and r["log_config"].get("max-file")
        )
    ]
    nnp = [s for s, r in res.items() if "no-new-privileges:true" not in (r["security_opt"] or [])]
    record(
        "hardening.no_new_privileges",
        "PASS" if not nnp else "WARN",
        "no-new-privileges set on every service"
        if not nnp
        else f"services without no-new-privileges (compose.yaml hardening gap): {nnp}",
    )
    record(
        "resources.limits_log_rotation",
        "PASS" if not bad else "FAIL",
        "every service has memory+cpu limit and json-file rotation (max-size/max-file)",
        violations=bad,
        per_service=res,
    )


def check_secrets(project: str, cs: dict[str, str], web: str) -> None:
    for name, c in (
        ("POSTGRES_PASSWORD", cs["db"]),
        ("MUSIC_WRITER_PASSWORD", cs["db"]),
        ("MUSIC_API_PASSWORD", cs["db"]),
    ):
        v = getenv(c, name)
        if len(v) >= 8:
            SECRETS.append(v)
    token = getenv(cs["worker"], "LISTENBRAINZ_TOKEN")
    if len(token) >= 8:
        SECRETS.append(token)
    record(
        "secrets.token_scan_scope",
        "INFO",
        "LISTENBRAINZ_TOKEN is "
        + (
            "set: scanned for literally"
            if len(token) >= 8
            else "EMPTY in this stack: a literal-token scan is NOT meaningful. Scans below "
            "cover the 3 DB "
            "passwords literally plus credential-shaped patterns (URL passwords, Token headers, "
            "TOKEN=/PASSWORD= assignments)"
        ),
        secret_values_scanned=len(SECRETS),
    )
    imgs: dict[str, str] = {}
    for svc in ("api", "worker", "web", "db"):
        imgs[svc] = json.loads(run(["docker", "inspect", cs[svc]])[1])[0]["Image"]
    blobs: dict[str, str] = {}
    env_bad: dict[str, Any] = {}
    collect_errors: list[str] = []
    stale = {}
    for svc, cid in cs.items():
        info = json.loads(run(["docker", "inspect", cid])[1])[0]
        tagged = json.loads(run(["docker", "image", "inspect", info["Config"]["Image"]])[1] or "[]")
        if not tagged or tagged[0]["Id"] != info["Image"]:
            stale[svc] = info["Config"]["Image"]
    record(
        "containers.run_current_images",
        "PASS" if not stale else "FAIL",
        "every container runs the image its tag currently points to"
        if not stale
        else "containers running an image different from their current tag (recreate them)",
        stale=stale,
    )
    for img in sorted(set(imgs.values())):
        rc1, insp, _ = run(["docker", "image", "inspect", img])
        rc2, hist, _ = run(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", img])
        if rc1 or rc2 or not insp.strip() or not hist.strip() or insp.strip() == "[]":
            collect_errors.append(
                f"image {img[7:19]}: inspect rc={rc1} history rc={rc2} or empty "
                "(container runs an image that no longer exists locally: stale, recreate it)"
            )
            continue
        blobs[f"image-inspect:{img[7:19]}"] = insp
        blobs[f"image-history:{img[7:19]}"] = hist
        envs = json.loads(insp)[0]["Config"].get("Env") or []
        names = [e.split("=", 1)[0] for e in envs]
        env_bad[img[7:19]] = {
            "names": names,
            "unexpected": [n for n in names if n not in ALLOWED_IMAGE_ENV],
            "secret_like": [n for n in names if SECRET_NAME.search(n) and n != "GPG_KEY"],
        }
    bad_env = {k: v for k, v in env_bad.items() if v["unexpected"] or v["secret_like"]}
    record(
        "image.env_allowlist",
        "PASS" if not bad_env else "FAIL",
        "image Config.Env contains only expected build/runtime variables, no secret-named "
        "variables "
        "(names only reported; compose runtime env is not part of the image)",
        per_image=env_bad,
    )
    with tempfile.TemporaryDirectory() as td:
        rc, _, _ = run(["docker", "cp", f"{web}:/usr/share/nginx/html/.", td])
        files = [p for p in Path(td).rglob("*") if p.is_file()]
        if (
            rc
            or not (Path(td) / "index.html").is_file()
            or not any(p.suffix == ".js" for p in files)
        ):
            collect_errors.append(f"client bundle copy rc={rc}, missing index.html or *.js")
        for p in files:
            blobs[f"client:{p.relative_to(td)}"] = p.read_text(errors="ignore")
        n_client = len(files)
    logs = ""
    for svc, cid in cs.items():
        rc, lo, le = run(["docker", "logs", cid])
        if rc or not (lo + le).strip():
            collect_errors.append(f"logs {svc}: rc={rc} or empty")
        logs += lo + le
    blobs["logs"] = logs
    if len(SECRETS) < 3:
        collect_errors.append(f"only {len(SECRETS)} of 3 DB passwords readable for literal scan")
    hits: dict[str, list[str]] = {}
    for label, text in blobs.items():
        for s in SECRETS:
            if s in text:
                hits.setdefault(label.split(":")[0], []).append("literal-secret")
        for pn, rx in CRED_PATTERNS.items():
            if rx.search(text):
                hits.setdefault(label.split(":")[0], []).append(pn)
    surfaces = {
        "images": len([k for k in blobs if k.startswith("image")]),
        "client_files": n_client,
        "log_bytes": len(blobs["logs"]),
    }
    record(
        "leak.scan",
        "PASS" if not hits and not collect_errors else "FAIL",
        "no literal DB password and no credential-shaped strings in image inspect/history, built "
        "client bundle, or container logs"
        if not hits and not collect_errors
        else "scan incomplete or credential-like content found (values withheld)",
        collect_errors=collect_errors,
        hits=hits,
        surfaces=surfaces,
    )


def acl_snapshot(db: str, dbname: str) -> dict[str, str]:
    q = (
        "SELECT 'rel:'||c.relname, pg_get_userbyid(c.relowner)||' '||coalesce(c.relacl, "
        "acldefault((CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END)::\"char\", c.relowner))::text"
        " FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"
        " AND c.relkind IN ('r','S','p') UNION ALL SELECT 'schema:'||nspname,"
        " pg_get_userbyid(nspowner)||' '||coalesce(nspacl,"
        " acldefault(CAST('n' AS \"char\"), nspowner))::text "
        "FROM pg_namespace"
        " WHERE nspname='public' ORDER BY 1"
    )
    return dict(line.split("|", 1) for line in psql(db, dbname, q).splitlines())


def counts(db: str, dbname: str) -> dict[str, int]:
    tabs = psql(
        db, dbname, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
    ).split()
    return {t: int(psql(db, dbname, f"SELECT count(*) FROM public.{t}")) for t in tabs}


def drop_db(db: str, name: str) -> None:
    psql(db, "postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def check_backup_rebuild(a: argparse.Namespace, cs: dict[str, str]) -> None:
    db, api = cs["db"], cs["api"]
    R = "md_integ_restore"
    tzs = a.tz or ["UTC", "Europe/Madrid"]
    common = {"families": CHARTS, "tzs": tzs, "from": a.from_date, "to": a.to_date}
    live = helper(api, None, mode="charts", db="music_dashboard", **common)
    if "error" in live:
        record("backup.restore_compare", "SKIP", "live DB has no listens (dataset not ready)")
        return
    n_live = len(live["charts"])
    created = False
    if psql(db, "postgres", f"SELECT 1 FROM pg_database WHERE datname='{R}'"):
        record(
            "backup.restore_compare",
            "FAIL",
            f"refusing: database {R} already exists; it is never dropped by this script "
            "(owner must remove it deliberately)",
        )
        return
    try:
        psql(db, "postgres", f"CREATE DATABASE {R} OWNER music_writer")
        created = True
        psql(db, "postgres", f"REVOKE ALL ON DATABASE {R} FROM PUBLIC")
        psql(db, "postgres", f"GRANT CONNECT ON DATABASE {R} TO music_writer, music_api")
        for cmd in (
            ["pg_dump", "-U", "postgres", "-Fc", "-f", "/tmp/md_integ.dump", "music_dashboard"],
            ["pg_restore", "-U", "postgres", "--exit-on-error", "-d", R, "/tmp/md_integ.dump"],
        ):
            code, _, err = run(["docker", "exec", db, *cmd])
            if code:
                raise RuntimeError(redact(err[-400:]))
        src_acl, dst_acl = acl_snapshot(db, "music_dashboard"), acl_snapshot(db, R)
        diff = {
            k: {"src": src_acl.get(k), "restored": dst_acl.get(k)}
            for k in set(src_acl) | set(dst_acl)
            if src_acl.get(k) != dst_acl.get(k)
        }
        schema_only = diff and all(k.startswith("schema:") for k in diff)
        if not diff:
            record(
                "backup.acl_preserved",
                "PASS",
                "pg_dump/pg_restore preserved owners + ACLs of all public tables/sequences "
                "and schema",
                objects=len(src_acl),
            )
        else:
            record(
                "backup.acl_preserved",
                "WARN" if schema_only else "FAIL",
                "restored ACL/owner differs from source"
                + (
                    " ONLY at schema level (pg_dump does not carry public-schema owner/ACL; "
                    "restore "
                    "runbook needs: ALTER SCHEMA public OWNER TO music_writer; GRANT USAGE "
                    "ON SCHEMA "
                    "public TO music_writer, music_api). Applied in the temp DB to continue."
                    if schema_only
                    else ""
                ),
                differences=diff,
            )
            if schema_only:
                psql(
                    db,
                    R,
                    "ALTER SCHEMA public OWNER TO music_writer; "
                    "GRANT USAGE ON SCHEMA public TO music_writer, music_api",
                )
                again = acl_snapshot(db, R)
                record(
                    "backup.acl_after_runbook_fix",
                    "PASS" if again == src_acl else "FAIL",
                    "ACLs equal source after the documented schema-level fix",
                )
        c_src, c_dst = counts(db, "music_dashboard"), counts(db, R)
        record(
            "backup.row_counts",
            "PASS" if c_src == c_dst else "FAIL",
            "per-table row counts identical after restore",
            counts=c_dst,
            differing={k for k in c_src if c_src[k] != c_dst.get(k)},
        )
        rest = helper(api, None, mode="charts", db=R, **common)
        same_full = [
            k
            for k in live["charts"]
            if live["charts"][k]["full"] == rest["charts"].get(k, {}).get("full")
        ]
        record(
            "backup.charts_byte_identical",
            "PASS" if len(same_full) == n_live else "FAIL",
            f"{len(same_full)}/{n_live} chart responses (10 charts x {len(tzs)} tz, range "
            f"{live['range'][0]}..{live['range'][1]}) byte-identical canonical JSON, live "
            f"vs restored DB "
            "via SQLStatisticsQueries as music_api (generation/last_updated included)",
            mismatched=sorted(set(live["charts"]) - set(same_full)),
            sample_sha256={k: v["full"][:16] for k, v in list(live["charts"].items())[:3]},
            sizes={k: v["bytes"] for k, v in live["charts"].items()},
        )
        raw = helper(
            api,
            None,
            mode="rawhttp",
            db=R,
            **{**common, "from": live["range"][0], "to": live["range"][1]},
        )
        if "error" in raw:
            record("api.raw_http_byte_identity", "FAIL", raw["error"])
        else:
            rr = raw["responses"]
            fams = {k.split("|")[0][len("/api/") :] for k in rr if k != "/api/health"}
            n_expected = len(CHARTS) * len(tzs)
            coverage_bad = (
                []
                if (
                    fams == set(CHARTS)
                    and len(rr) - 1 == n_expected
                    and all(v["bytes"] > 0 for v in rr.values())
                )
                else ["expected exactly 10 chart families x tz with non-empty bodies"]
            )
            bad = coverage_bad + [
                k
                for k, v in rr.items()
                if k != "/api/health"
                and (
                    v["live_sha"] != v["restored_sha"]
                    or v["live_status"] != 200
                    or v["restored_status"] != 200
                )
            ]
            record(
                "api.raw_http_byte_identity",
                "PASS" if not bad else "FAIL",
                f"{len(rr) - 1} raw HTTP chart bodies (health excluded: sync_lag_seconds is "
                f"dynamic; 10 charts x {len(tzs)} tz) byte-identical "
                "(sha256 of raw bytes): running API vs a temporary uvicorn on the restored "
                "DB bound to "
                f"in-container loopback :{raw['port']} (not published); both via real ASGI stack, "
                "both MUSIC_FIXTURE_MODE=false as music_api",
                mismatched=bad,
                families_covered=sorted(fams),
                body_bytes={k: v["bytes"] for k, v in rr.items()},
            )
        check_rebuild(a, cs, R, common, rest)
    except Exception as exc:
        record(
            "backup.restore_compare", "FAIL", f"backup/restore proof aborted: {redact(str(exc))}"
        )
    finally:
        run(["docker", "exec", db, "rm", "-f", "/tmp/md_integ.dump"])
        if created and not a.keep_restore:
            try:
                drop_db(db, R)
            except Exception:
                pass


POISON = [
    ("delete_third_of_listens", "DELETE FROM listens WHERE id % 3 = 0"),
    ("corrupt_track_names", "UPDATE listens SET track_name = 'POISON-' || id WHERE id % 5 = 1"),
    (
        "insert_phantom_listens_uuid_msid",
        "INSERT INTO listens (user_name, listened_at, recording_msid, track_name, "
        "artist_name, release_name)"
        " SELECT user_name, listened_at + interval '1 second', gen_random_uuid(), "
        "'PHANTOM', artist_name,"
        " release_name FROM listens LIMIT 5",
    ),
    ("drop_listen_artists", "DELETE FROM listen_artists WHERE listen_id % 2 = 0"),
    ("wipe_entity_genres", "DELETE FROM entity_genres"),
    ("wipe_artist_countries", "DELETE FROM artist_countries"),
    ("wipe_genres", "DELETE FROM genres WHERE true"),
]
INPUT_TABLES = [
    "raw_listens",
    "metadata_cache",
    "quarantined_listens",
    "sync_runs",
    "sync_state",
    "enrichment_queue",
]


def input_fingerprint(db: str, dbname: str) -> dict[str, str]:
    return {
        t: psql(
            db,
            dbname,
            f"SELECT coalesce(md5(string_agg(x::text,'' ORDER BY x::text)),'empty')"
            f" FROM public.{t} x",
        )
        for t in INPUT_TABLES
    }


def gens(db: str, dbname: str) -> tuple[int, int, str]:
    i, e, lu = psql(
        db,
        dbname,
        "SELECT ingestion_generation, enrichment_generation, last_updated"
        " FROM data_generation WHERE id=1",
    ).split("|")
    return int(i), int(e), lu


def json_diff(a: Any, b: Any, path: str = "$", out: list[Any] | None = None) -> dict[str, Any]:
    """Exact structural diff (first 12 differing leaves) plus an order-only hint."""
    top = out is None
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
            else:
                json_diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            json_diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))
    if not top:
        return {}

    def ser(v: Any) -> str:
        return json.dumps(v, sort_keys=True)

    order_only = (
        isinstance(a, dict)
        and isinstance(b, dict)
        and all(
            isinstance(a.get(k), list)
            and isinstance(b.get(k), list)
            and sorted(map(ser, a[k])) == sorted(map(ser, b[k]))
            for k in a
            if isinstance(a.get(k), list) and a.get(k) != b.get(k)
        )
    )
    return {
        "_leaves": out,
        "differing_leaves": len(out),
        "first": [(p, str(x)[:120], str(y)[:120]) for p, x, y in out[:12]],
        "lists_differ_only_by_order": order_only,
    }


def float_noise_only(leaves: list[Any], rel_tol: float = 1e-9) -> float | None:
    """Max relative delta if EVERY differing leaf is a numeric series value within rel_tol.

    Any structural change (keys, labels, order, bucket lists, lengths, non-numbers) -> None.
    """
    worst = 0.0
    for path, x, y in leaves:
        numeric = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (x, y))
        if not numeric or not re.search(r"\.series\[\d+\]\.values\[\d+\]$", path):
            return None
        rel = abs(x - y) / max(abs(x), abs(y), 1e-300)
        if rel > rel_tol:
            return None
        worst = max(worst, rel)
    return worst


def check_rebuild(
    a: argparse.Namespace,
    cs: dict[str, str],
    R: str,
    common: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    """Wipe/poison projections in the RESTORED copy only, rebuild offline, compare to baseline.

    Baseline = restored DB charts (already proven byte-identical to live). Rebuild bumps both
    generations by 1 and preserves last_updated, so comparison excludes only generation fields.
    """
    db, worker, api = cs["db"], cs["worker"], cs["api"]
    _, helptxt, _ = run(["docker", "exec", worker, "music-dashboard", "--help"])
    cmd = a.rebuild_cmd or ("rebuild" if re.search(r"\brebuild\b", helptxt) else None)
    if not cmd:
        record("rebuild.determinism", "SKIP", "no rebuild CLI command in this build; NOT proven")
        return
    argv = ["music-dashboard", cmd]
    n = len(baseline["charts"])

    def charts(with_json: bool = False) -> dict[str, Any]:
        return helper(api, None, mode="charts", db=R, json=with_json, **common)["charts"]

    def cmp(ch: dict[str, Any], key: str) -> list[str]:
        return [k for k in baseline["charts"] if baseline["charts"][k][key] != ch[k][key]]

    fp0, g0 = input_fingerprint(db, R), gens(db, R)
    base_json = {k: v["json"] for k, v in charts(True).items()}
    applied, failed = [], []
    for label, sql in POISON:
        try:
            psql(db, R, sql)
            applied.append(label)
        except RuntimeError as exc:
            failed.append(f"{label}: {str(exc)[:120]}")
    poisoned = charts()
    differ = cmp(poisoned, "stripped")
    record(
        "rebuild.poison_effective",
        "PASS" if differ else "FAIL",
        f"poisoning projections changed {len(differ)}/{n} charts (test is non-vacuous only if >0)",
        applied=applied,
        not_applied=failed,
        changed_charts=differ[:20],
    )
    if not differ:
        return
    g1 = gens(db, R)
    dr = helper(worker, None, mode="cli", db=R, argv=[*argv, "--dry-run"])
    dry_charts = charts()
    after_dry = [k for k in poisoned if poisoned[k]["full"] != dry_charts[k]["full"]]
    record(
        "rebuild.dry_run_changes_nothing",
        "PASS" if dr["returncode"] == 0 and not after_dry and gens(db, R) == g1 else "FAIL",
        "rebuild --dry-run exit 0; every poisoned chart hash (full, incl. generation/freshness) "
        "identical before and after; generations and last_updated unchanged",
        changed_by_dry_run=after_dry,
        returncode=dr["returncode"],
        stdout_tail=dr["stdout_tail"],
        stderr_tail=dr["stderr_tail"],
    )
    r1 = helper(worker, None, mode="cli", db=R, argv=argv)
    if r1["returncode"]:
        record(
            "rebuild.determinism",
            "FAIL",
            f"rebuild exit {r1['returncode']}",
            stderr_tail=r1["stderr_tail"],
            stdout_tail=r1["stdout_tail"],
        )
        return
    c1, g2 = charts(), gens(db, R)
    bad_strip, bad_full = cmp(c1, "stripped"), cmp(c1, "full")
    exact_diff: dict[str, Any] = {}
    tolerated: dict[str, float] = {}
    strict_bad = list(bad_strip)
    if bad_strip:
        after_json = charts(True)
        for k in bad_strip:
            d = json_diff(json.loads(base_json[k]), json.loads(after_json[k]["json"]))
            leaves = d.pop("_leaves")
            if len(exact_diff) < 4:
                exact_diff[k] = d
            worst = float_noise_only(leaves) if k.startswith("evolution/") else None
            if worst is not None:
                tolerated[k] = worst
                strict_bad.remove(k)
    record(
        "rebuild.restores_poisoned_projections",
        "PASS" if not strict_bad and g2[2] == g0[2] else "FAIL",
        f"after rebuild {n - len(bad_strip)}/{n} charts byte-identical to pre-poison "
        "baseline excluding the generation field (rebuild legitimately bumps generation; "
        f"{n - len(bad_full)}/{n} identical including it). "
        f"{len(tolerated)} evolution chart(s) differ ONLY by float last-bit noise in series "
        f"values (max relative delta {max(tolerated.values(), default=0):.2e}, tolerance 1e-9; "
        "everything else exact, no structural/label/order/bucket diff); "
        f"{len(strict_bad)} chart(s) differ beyond that. last_updated must be preserved.",
        mismatched=strict_bad[:20],
        tolerated_float_noise_max_rel_delta=tolerated,
        exact_diff_first_mismatches=exact_diff,
        generation_before_poison=g0[:2],
        generation_after=g2[:2],
        last_updated_preserved=g2[2] == g0[2],
        stdout_tail=r1["stdout_tail"],
    )
    record(
        "rebuild.generation_bump",
        "PASS" if (g2[0], g2[1]) == (g1[0] + 1, g1[1] + 1) and g2[2] == g1[2] == g0[2] else "FAIL",
        "both generations advanced by exactly 1 (cache invalidation) and last_updated preserved"
        if g2[2] == g0[2]
        else "last_updated changed by rebuild",
        before=g1[:2],
        after=g2[:2],
    )
    record(
        "rebuild.inputs_untouched",
        "PASS" if input_fingerprint(db, R) == fp0 else "FAIL",
        "raw_listens, metadata_cache, quarantine, sync_*, enrichment_queue unchanged (md5 "
        "of all rows)",
    )
    r2 = helper(worker, None, mode="cli", db=R, argv=argv)
    c2 = charts()
    record(
        "rebuild.idempotent",
        "PASS"
        if r2["returncode"] == 0 and all(c2[k]["stripped"] == c1[k]["stripped"] for k in c1)
        else "FAIL",
        "second rebuild leaves charts identical to the first rebuild (excluding generation)",
        returncode=r2["returncode"],
    )


def check_load(a: argparse.Namespace, api: str) -> None:
    probe = helper(
        api,
        None,
        mode="charts",
        db="music_dashboard",
        families=["heatmap"],
        tzs=["UTC"],
        **{"from": a.from_date, "to": a.to_date},
    )
    if "error" in probe:
        record("load.measured", "SKIP", "no data")
        return
    f, t = probe["range"]
    r = helper(
        api,
        None,
        mode="load",
        **{"from": f, "to": t},
        tz=(a.tz or ["UTC"])[0],
        families=CHARTS,
        concurrency=8,
    )
    record(
        "load.measured",
        "INFO",
        "measured latency only; NO SLA/threshold asserted; single host, "
        "tiny dataset unless parent seeded more, include container CPU limit 1.0",
        **r,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:18150")
    ap.add_argument("--project", default="md-integ")
    ap.add_argument("--out", default="/tmp/md-integration-security")
    ap.add_argument("--tz", action="append")
    ap.add_argument("--from", dest="from_date")
    ap.add_argument("--to", dest="to_date")
    ap.add_argument("--burst", action="store_true", help="run proxy 429 burst (opt-in; hits nginx)")
    ap.add_argument("--keep-restore", action="store_true")
    ap.add_argument("--rebuild-cmd")
    ap.add_argument("--rebuild-arg", action="append", default=[])
    a = ap.parse_args()
    cs = {s: container(a.project, s) for s in ("db", "api", "worker", "web")}
    steps = [
        ("source", lambda: check_source(cs["api"], cs["worker"])),
        ("secrets", lambda: check_secrets(a.project, cs, cs["web"])),
        ("ports", lambda: check_ports_resources(a.project, cs)),
        ("roles", lambda: check_roles(cs["db"], cs["api"])),
        ("routes", lambda: check_routes(cs["api"], a.base_url)),
        ("cache", lambda: check_cache(cs["api"], cs["db"])),
        ("backup", lambda: check_backup_rebuild(a, cs)),
        ("load", lambda: check_load(a, cs["api"])),
    ]
    if a.burst:
        steps.append(("burst", lambda: check_burst(a.base_url)))
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            where = f"{Path(tb.filename).name}:{tb.lineno} in {tb.name}"
            record(
                f"{name}.error",
                "FAIL",
                f"step crashed at {where}: {type(exc).__name__}: {redact(str(exc))[:300]}",
            )
    limits = [
        "Token scan: LISTENBRAINZ_TOKEN empty here; literal token absence is unprovable. Only DB "
        "passwords + credential-shaped patterns are scanned.",
        "Load numbers: loopback inside api container, single run, no SLA; nginx 10r/s limit "
        "bypassed.",
        "Rebuild proof runs on the restored copy only; it proves projections re-derive from "
        "raw+cache for that dataset, not equality with data that was never in raw/cache. "
        "Fully populated live DB is never rebuilt by this script.",
        "Restore proof uses a temp DB; role-level settings "
        "(statement_timeout etc.) live in the cluster and are not carried by pg_dump of one "
        "database.",
        "Cache bounds: unit test evidence referenced, not re-run; runtime test uses a small cache.",
    ]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rep = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": a.project,
        "results": RESULTS,
        "limitations": limits,
    }
    (out / "report.json").write_text(redact(json.dumps(rep, indent=2, default=str)))
    md = ["# md-integ security/integration proof", ""]
    md += [f"- **{r['status']}** `{r['id']}` — {r['summary']}" for r in RESULTS]
    md += ["", "## Limitations", *[f"- {x}" for x in limits]]
    (out / "report.md").write_text(redact("\n".join(md)) + "\n")
    fails = [r["id"] for r in RESULTS if r["status"] == "FAIL"]
    print(f"\nreport: {out}/report.json, report.md; FAIL={len(fails)} {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
