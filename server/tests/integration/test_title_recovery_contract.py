"""T3 proof: ranking title recovery contract (disposable PostgreSQL, synthetic data only).

Contract under test (T1/T2 split):
* `release_title_cache(entity_type, entity_mbid, title)` is POSITIVE ONLY (as migrated, fetched_at
  lives in the private lookup). music_api gets column SELECT on exactly those 3 columns.
* `release_title_lookup` is private (status / retry metadata): no API privileges at all.
* `SqlTitleCacheRepository.save_resolved(type, mbid, title, release_group_mbid, next_retry_at, now)`
  -> bool and `save_miss(type, mbid, status, next_retry_at, now, attempts)`.
* Publication bumps enrichment_generation only; ingestion_generation and last_updated are
  never touched. Rankings recover labels only (keys, mbids, counts, coverage unchanged).

Env: TEST_DATABASE_URL / TEST_API_DATABASE_URL (see conftest). Timing thresholds can be
overridden with TITLE_PERF_COLD_WIDE_MS. Everything here is synthetic.
"""

import json
import os
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.domain.statistics.policies import StatisticsUnavailable
from music_dashboard.infrastructure.persistence.repositories.title_cache import (
    SqlTitleCacheRepository,
)
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

from .conftest import alembic_cfg

pytestmark = pytest.mark.integration

REL = "ec30b077-3364-41de-84c1-fbd6612e1e1e"  # synthetic reproduction ids (release)
RG = "98fb14cc-9134-479b-baa6-18a881e18dca"  # (release group = album ranking key)
COVER = f"https://coverartarchive.org/release-group/{RG}/front-500"
TITLE = "<LOVE&POP> pt.1"
API_COLS = {"entity_type", "entity_mbid", "title"}
CACHE_COLS = API_COLS  # fetched_at / status / retry live only in private release_title_lookup
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
RETRY = NOW + timedelta(days=30)


# ---------------------------------------------------------------- helpers
@pytest.fixture
def writer(migrated_db, admin_url):
    eng = create_engine(admin_url)
    yield eng
    eng.dispose()


@pytest.fixture
def api(migrated_db, api_url):
    eng = create_engine(api_url)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(writer):
    return SqlTitleCacheRepository(writer)


def filters(start, end=None, limit=10):
    return StatisticsFilter(
        date.fromisoformat(start), date.fromisoformat(end or start), "UTC", limit
    )


def add_listens(conn, rows):
    """rows: dicts with at, artist, album, rg, rel."""
    for r in rows:
        conn.execute(
            text(
                "INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,"
                "release_name,release_group_mbid,release_mbid) VALUES ('t3',"
                "CAST(:at AS timestamptz),CAST(:id AS uuid),:artist,:album,"
                "CAST(:rg AS uuid),CAST(:rel AS uuid))"
            ),
            {
                "at": r["at"],
                "id": str(uuid.uuid4()),
                "artist": r.get("artist", "Synthetic Artist"),
                "album": r.get("album"),
                "rg": r.get("rg"),
                "rel": r.get("rel"),
            },
        )


def set_generation(conn, ingestion=7, last_updated="2026-01-01T00:00:00Z"):
    conn.execute(
        text(
            "UPDATE data_generation SET ingestion_generation=:i, enrichment_generation=0,"
            " last_updated=CAST(:l AS timestamptz) WHERE id=1"
        ),
        {"i": ingestion, "l": last_updated},
    )


def generation(conn):
    return conn.execute(
        text(
            "SELECT ingestion_generation, enrichment_generation, last_updated FROM data_generation"
        )
    ).one()


def albums(q, f):
    return q.query("rankings/albums", f)


def items_by_key(resp):
    return {i["key"]: i for i in resp["items"]}


def plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def explain(writer, statement, params, jit_off=True):
    """EXPLAIN ANALYZE on an independent writer connection. Mirrors the endpoint's
    `SET LOCAL jit = off` for albums/tracks rankings (default True) so plan execution times
    reflect what the API executes; the endpoint wall-time assertions separately prove the
    endpoint really disables JIT. "Cold" in this file means application-cold (fresh
    SQLStatisticsQueries cache / fresh pooled connection), NOT physically cold storage:
    the buffer cache and OS cache are warm after seeding."""
    with writer.begin() as c:
        if jit_off:
            c.exec_driver_sql("SET LOCAL jit = off")
        raw = c.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
        return raw.scalar_one()[0]


# ---------------------------------------------------------------- grants / shape
def test_cache_table_shape_and_api_column_grants(migrated_db, api):
    with migrated_db.connect() as c:
        cols = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='release_title_cache'"
                )
            )
        }
        assert cols == CACHE_COLS, cols
        for col in sorted(CACHE_COLS):
            allowed = c.execute(
                text("SELECT has_column_privilege('music_api','release_title_cache',:c,'SELECT')"),
                {"c": col},
            ).scalar()
            assert allowed is (col in API_COLS), col
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not c.execute(
                text("SELECT has_table_privilege('music_api','release_title_cache',:p)"),
                {"p": priv},
            ).scalar()
            assert not c.execute(
                text("SELECT has_table_privilege('music_api','release_title_lookup',:p)"),
                {"p": priv},
            ).scalar()
        # table-level SELECT must not exist (would expose fetched_at); lookup fully private
        assert not c.execute(
            text("SELECT has_table_privilege('music_api','release_title_cache','SELECT')")
        ).scalar()
        assert not c.execute(
            text("SELECT has_table_privilege('music_api','release_title_lookup','SELECT')")
        ).scalar()
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM information_schema.column_privileges "
                    "WHERE grantee='PUBLIC' AND table_name IN "
                    "('release_title_cache','release_title_lookup')"
                )
            ).scalar()
            == 0
        )
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            for t in ("release_title_cache", "release_title_lookup"):
                assert c.execute(
                    text("SELECT has_table_privilege('music_writer',:t,:p)"), {"t": t, "p": p}
                ).scalar(), (t, p)

    with api.connect() as c:
        c.execute(text("SELECT entity_type, entity_mbid, title FROM release_title_cache")).all()
    denied = [
        "SELECT fetched_at FROM release_title_lookup",
        "SELECT status FROM release_title_cache",
        "SELECT count(*) FROM release_title_lookup",
        "SELECT * FROM release_title_lookup",
        "SELECT status FROM release_title_lookup",
        "INSERT INTO release_title_cache(entity_type,entity_mbid,title) "
        f"VALUES ('release_group','{RG}','x')",
        "UPDATE release_title_cache SET title='x'",
        "DELETE FROM release_title_cache",
        "TRUNCATE release_title_cache",
    ]
    for sql in denied:
        with api.connect() as c, pytest.raises(DBAPIError) as exc:
            c.execute(text("SET TRANSACTION READ WRITE"))  # prove grants, not the RO default
            c.execute(text(sql))
            pytest.fail(f"not denied: {sql}")
        # permission denied (42501) -- not merely read-only-transaction (25006)
        # 42501 permission denied; 42703 = column deliberately absent from the public table
        expected = {"42501", "42703"} if "FROM release_title_cache" in sql else {"42501"}
        assert getattr(exc.value.orig, "sqlstate", None) in expected, (sql, exc.value)


def test_negative_metadata_is_private_and_cache_stays_positive_only(repo, writer):
    repo.save_miss("release_group", RG, "not_found", RETRY, NOW, 1)
    repo.save_miss("release", REL, "transient", RETRY, NOW, 2)
    with writer.connect() as c:
        assert c.execute(text("SELECT count(*) FROM release_title_cache")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM release_title_lookup")).scalar() == 2
        assert generation(c).enrichment_generation == 0  # a miss never changes a label


# ---------------------------------------------------------------- migration rollback
def test_downgrade_0006_to_0005_keeps_refresh_schema_and_upgrade_again(migrated_db, admin_url):
    # Seed the 0005 singleton with a recognizable state; it must survive the 0006 round trip.
    api_engine = create_engine(os.environ["TEST_API_DATABASE_URL"])
    try:
        with migrated_db.connect() as c:
            c.execute(text("UPDATE refresh_state SET detail='kept_marker'"))
        cfg = alembic_cfg(admin_url)
        command.downgrade(cfg, "0005")
        with migrated_db.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005"
            tables = {
                r[0]
                for r in c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public'"
                    )
                )
            }
            assert not tables & {"release_title_cache", "release_title_lookup"}
            assert "refresh_state" in tables
            assert c.execute(text("SELECT detail FROM refresh_state")).scalar() == "kept_marker"
            fns = {
                r[0]
                for r in c.execute(
                    text(
                        "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                        "WHERE n.nspname='public' AND proname LIKE 'public_refresh_%'"
                    )
                )
            }
            assert fns == {"public_refresh_request", "public_refresh_status"}
            assert (
                c.execute(text("SELECT to_regclass('public.ix_listens_release_mbid')")).scalar()
                is None
            )
        with api_engine.connect() as c:
            c.execute(text("SELECT * FROM public_refresh_status()")).all()
            assert not c.execute(
                text("SELECT has_table_privilege('music_api','refresh_state','SELECT')")
            ).scalar()
        command.upgrade(cfg, "head")
        with migrated_db.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
            assert c.execute(text("SELECT detail FROM refresh_state")).scalar() == "kept_marker"
            assert {
                r[0]
                for r in c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='release_title_cache'"
                    )
                )
            } == CACHE_COLS
    finally:
        api_engine.dispose()


def test_0006_depends_on_0005_refresh_surface(migrated_db):
    with migrated_db.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
        for fn in ("public_refresh_request()", "public_refresh_status()"):
            assert c.execute(
                text("SELECT has_function_privilege('music_api',:f,'EXECUTE')"),
                {"f": f"public.{fn}"},
            ).scalar()


# ---------------------------------------------------------------- publication semantics
def test_publication_bumps_only_enrichment_and_only_on_visible_change(repo, writer):
    with writer.begin() as c:
        set_generation(c)
        before = generation(c)
    assert repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW) is True
    with writer.connect() as c:
        after = generation(c)
    assert (after.ingestion_generation, after.last_updated) == (
        before.ingestion_generation,
        before.last_updated,
    )
    assert after.enrichment_generation == 1
    # identical re-resolve: refresh retry only, no label change, no bump
    assert repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW) is False
    # a later miss never replaces a positive title or bumps
    repo.save_miss("release_group", RG, "transient", RETRY, NOW, 3)
    with writer.connect() as c:
        assert generation(c).enrichment_generation == 1
        assert (
            c.execute(
                text("SELECT title FROM release_title_cache WHERE entity_mbid=CAST(:m AS uuid)"),
                {"m": RG},
            ).scalar()
            == TITLE
        )
    # changed title is a visible change
    assert repo.save_resolved("release_group", RG, TITLE + " v2", None, RETRY, NOW) is True
    with writer.connect() as c:
        assert generation(c).enrichment_generation == 2


def test_invalid_publication_is_atomic(repo, writer):
    with writer.begin() as c:
        set_generation(c)
    with pytest.raises(DBAPIError):
        repo.save_resolved("release_group", RG, "   ", None, RETRY, NOW)  # blank title
    with pytest.raises(DBAPIError):
        repo.save_resolved("bogus", RG, "x", None, RETRY, NOW)
    with writer.connect() as c:
        assert c.execute(text("SELECT count(*) FROM release_title_cache")).scalar() == 0
        g = generation(c)
        assert (g.ingestion_generation, g.enrichment_generation) == (7, 0)


def test_readers_never_see_generation_without_row_or_row_without_generation(repo, writer, api):
    """Publication atomicity: in one snapshot, enrichment_generation == published rows."""
    with writer.begin() as c:
        set_generation(c)
    stop = threading.Event()
    violations: list[tuple[int, int]] = []

    def reader():
        with api.connect() as c:
            while not stop.is_set():
                with c.begin():
                    c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                    g = c.execute(
                        text("SELECT enrichment_generation FROM data_generation")
                    ).scalar()
                    n = c.execute(text("SELECT count(*) FROM release_title_cache")).scalar()
                if g != n:
                    violations.append((g, n))

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(60):
            repo.save_resolved("release_group", str(uuid.uuid4()), f"T{i}", None, RETRY, NOW)
    finally:
        stop.set()
        t.join(10)
    assert not violations, violations[:5]
    with writer.connect() as c:
        assert generation(c).enrichment_generation == 60


def test_cache_publication_concurrent_with_public_refresh_requests(repo, writer, api):
    """No lost generations, ingestion/last_updated preserved, exactly one coalesced request."""
    with writer.begin() as c:
        set_generation(c)
        before = generation(c)
    n_pub, n_req = 40, 40
    errors: list[BaseException] = []
    results: list[tuple] = []
    start = threading.Barrier(5)

    def publish(offset):
        try:
            start.wait()
            for i in range(n_pub // 4):
                repo.save_resolved(
                    "release_group", str(uuid.uuid4()), f"P{offset}-{i}", None, RETRY, NOW
                )
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def refresh():
        try:
            start.wait()
            for _ in range(n_req):
                with api.connect() as c:
                    c.execute(text("SET TRANSACTION READ WRITE"))
                    results.append(
                        tuple(c.execute(text("SELECT * FROM public_refresh_request()")).one())
                    )
                    c.commit()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=publish, args=(k,)) for k in range(4)]
    threads.append(threading.Thread(target=refresh))
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors, errors
    with writer.connect() as c:
        after = generation(c)
        rows = c.execute(text("SELECT count(*) FROM release_title_cache")).scalar()
        refresh_rows = c.execute(text("SELECT count(*) FROM refresh_state")).scalar()
    assert rows == n_pub
    assert after.enrichment_generation == n_pub  # no lost bumps
    assert after.ingestion_generation == before.ingestion_generation
    assert after.last_updated == before.last_updated
    assert refresh_rows == 1
    assert len(results) == n_req
    request_ids = {r[0] for r in results}
    assert len(request_ids) == 1  # first admitted, rest coalesced / cooling down
    assert sum(1 for r in results if r[8]) == n_req - 1  # coalesced flag (column 'coalesced')


# ---------------------------------------------------------------- ranking behaviour
def seed_narrow_wide(conn):
    """Target album appears on a narrow day with MBIDs only (no name) and widely elsewhere."""
    rows = []
    for i, day in enumerate(("2026-03-10", "2026-03-10", "2026-03-11", "2026-04-02", "2026-05-01")):
        rows.append({"at": f"{day}T10:{i:02d}:00Z", "rg": RG, "rel": REL})
    # a named, unrelated album so rankings are not trivially single-item
    rows += [
        {"at": "2026-03-10T11:00:00Z", "album": "Other Album", "rg": str(uuid.uuid4())},
        {"at": "2026-03-10T11:01:00Z", "album": "Name Only", "artist": "Nobody"},
    ]
    add_listens(conn, rows)


@pytest.fixture
def stats(migrated_db, writer, api, repo):
    with writer.begin() as c:
        seed_narrow_wide(c)
        set_generation(c)
        # existing safe CAA cover for the target album (keyed by the ranking key)
        c.execute(
            text(
                "INSERT INTO artwork(entity_type,entity_mbid,source,image_url,status) "
                "VALUES ('album',CAST(:m AS uuid),'caa',:u,'resolved')"
            ),
            {"m": RG, "u": COVER},
        )
    return SQLStatisticsQueries(api)


def strip_gen(resp):
    return {k: v for k, v in resp.items() if k not in ("generation", "last_updated", "lag")}


def snapshot_shape(resp):
    """Everything that must NOT change when a title is recovered."""
    return {
        "coverage": resp["coverage"],
        "items": [{k: v for k, v in i.items() if k != "label"} for i in resp["items"]],
    }  # (kept for debugging)


@pytest.mark.parametrize("window", [("2026-03-10", "2026-03-10"), ("2026-01-01", "2026-12-31")])
def test_narrow_and_wide_recover_title_without_changing_counts(stats, repo, window):
    q = stats
    f = filters(*window)
    before = albums(q, f)
    others = ("rankings/artists", "rankings/tracks", "pies/artists", "pies/genres")
    other_before = {x: strip_gen(q.query(x, f)) for x in others}
    assert RG not in items_by_key(before)  # unresolved nameless albums are not ranked
    repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW)
    after = albums(q, f)  # new generation invalidates the statistics cache
    other_after = {x: strip_gen(q.query(x, f)) for x in others}
    target = items_by_key(after)[RG]
    assert target["label"] == TITLE
    assert target["mbid"] == RG
    assert target["image_url"] == COVER  # existing non-null artwork kept exactly
    assert target["listens"] == (2 if window[0] == window[1] else 5)
    assert after["coverage"] == before["coverage"]  # coverage is overall, never changes
    # every previously ranked album keeps its label, key, listen count and image
    # (share is relative to the ranked population, which legitimately gains the recovered album)
    for k, item in items_by_key(before).items():
        got = items_by_key(after)[k]
        for field in ("label", "listens", "mbid", "image_url", "metadata_state"):
            assert got[field] == item[field], (k, field)
    # other charts are byte-for-byte unchanged by a label-only publication
    for family in ("rankings/artists", "rankings/tracks", "pies/artists", "pies/genres"):
        assert other_after[family] == other_before[family], family
    assert after["generation"] == before["generation"] + 1


def test_narrow_and_wide_use_same_label_and_exact_ids(stats, repo):
    repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW)
    narrow = items_by_key(albums(stats, filters("2026-03-10")))[RG]
    wide = items_by_key(albums(stats, filters("2026-01-01", "2026-12-31")))[RG]
    assert narrow["label"] == wide["label"] == TITLE
    assert narrow["mbid"] == wide["mbid"] == RG
    assert (narrow["listens"], wide["listens"]) == (2, 5)


def test_exact_release_then_group_fallback_and_stored_titles_win(stats, repo, writer):
    q = stats
    f = filters("2026-01-01", "2026-12-31")
    # exact release row exists, group row says something else: exact release wins
    repo.save_resolved("release", REL, "Exact Release Title", RG, RETRY, NOW)
    repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW)
    assert items_by_key(albums(q, f))[RG]["label"] == "Exact Release Title"
    # a stored listen title always wins over any cache row
    with writer.begin() as c:
        c.execute(
            text(
                "UPDATE listens SET release_name='Stored Title' WHERE id="
                "(SELECT min(id) FROM listens WHERE release_group_mbid=CAST(:g AS uuid))"
            ),
            {"g": RG},
        )
        c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))
    assert items_by_key(albums(q, f))[RG]["label"] == "Stored Title"


def test_group_fallback_when_no_exact_release_row(stats, repo):
    repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW)
    assert items_by_key(albums(stats, filters("2026-03-10")))[RG]["label"] == TITLE


def test_miss_rows_do_not_change_labels_or_generation(stats, repo, writer):
    q = stats
    f = filters("2026-03-10")
    before = albums(q, f)
    repo.save_miss("release_group", RG, "not_found", RETRY, NOW, 1)
    repo.save_miss("release", REL, "transient", RETRY, NOW, 1)
    after = albums(q, f)
    assert after == before


# ---------------------------------------------------------------- candidates
def test_candidates_dedup_include_release_when_group_present_and_bounded(repo, writer):
    with writer.begin() as c:
        add_listens(c, [{"at": "2026-03-10T10:00:00Z", "rg": RG, "rel": REL}] * 50)
        add_listens(c, [{"at": "2026-03-11T10:00:00Z", "rel": str(uuid.uuid4())}] * 3)
    cands = repo.candidates(NOW, 100)
    keys = [(c[0], c[1]) for c in cands]
    assert len(keys) == len(set(keys))  # deduplicated IDs
    assert ("release_group", RG) in keys
    assert ("release", REL) in keys  # release candidate even though RG is present
    assert len(repo.candidates(NOW, 1)) == 1
    # resolved ids are no longer candidates
    repo.save_resolved("release_group", RG, TITLE, None, RETRY, NOW)
    repo.save_resolved("release", REL, "Rel", RG, RETRY, NOW)
    keys = {(c[0], c[1]) for c in repo.candidates(NOW, 100)}
    assert ("release_group", RG) not in keys and ("release", REL) not in keys


# ---------------------------------------------------------------- performance
def seed_history(writer, n_history, distinct_albums):
    with writer.begin() as c:
        c.execute(
            text(
                """
            INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,release_name,
                                release_group_mbid)
            SELECT 'perf', timestamptz '2020-01-01' + (g * interval '10 minutes'),
                   md5('perf-'||g)::uuid, 'Artist '||(g % 500), 'Album '||(g % :albums),
                   md5('rg-'||(g % :albums))::uuid
            FROM generate_series(1,:n) g
            """
            ),
            {"n": n_history, "albums": distinct_albums},
        )
        c.execute(text("ANALYZE listens"))


def capture_album_sql(api, run):
    seen = []

    @event.listens_for(api, "before_cursor_execute")
    def grab(conn, cursor, statement, parameters, context, executemany):
        if "album_values" in statement and "release_title_cache" in statement:
            seen.append((statement, parameters))

    try:
        run()
    finally:
        event.remove(api, "before_cursor_execute", grab)
    return seen


def test_sparse_miss_narrow_window_no_full_history_scan_and_dedup(writer, api, repo):
    n = 60_000
    seed_history(writer, n, 3000)
    with writer.begin() as c:
        # sparse unnamed MBID listens (misses) in the narrow window: 400 plays of 2 ids
        add_listens(
            c,
            [
                {
                    "at": f"2026-06-01T{h:02d}:{m:02d}:00Z",
                    "rg": RG if m % 2 else None,
                    "rel": REL if not m % 2 else None,
                }
                for h in range(10)
                for m in range(40)
            ],
        )
        set_generation(c)
    q = SQLStatisticsQueries(api)
    f = filters("2026-06-01")
    timings = []
    seen = capture_album_sql(
        api,
        lambda: (
            timings.append(time.perf_counter()),
            albums(q, f),
            timings.append(time.perf_counter()),
        ),
    )
    ms = (timings[1] - timings[0]) * 1000
    print("TITLE_SPARSE_MISS_NARROW " + json.dumps({"history": n, "ms": round(ms, 1)}))
    assert seen, "album ranking must consult release_title_cache (T1 contract)"
    plan = explain(writer, *seen[0])
    nodes = list(plan_nodes(plan["Plan"]))
    listens_scans = [
        nd
        for nd in nodes
        if nd.get("Relation Name") == "listens"
        and nd["Node Type"].endswith("Scan")
        and nd.get("Actual Loops", 1) > 0  # never-executed (gated) nodes read nothing
    ]
    assert listens_scans
    seq = [nd for nd in listens_scans if nd["Node Type"] == "Seq Scan"]
    # Bounded: no scan of the full history (the 400 window rows + gated history only).
    rows_read = sum(
        nd["Actual Rows"] * nd["Actual Loops"] + nd.get("Rows Removed by Filter", 0)
        for nd in listens_scans
    )
    assert not seq, [(nd["Node Type"], nd["Actual Rows"]) for nd in seq]
    assert any(nd.get("Index Name") for nd in listens_scans)
    assert rows_read < n * 0.1, rows_read
    # IDs are deduplicated: cache probed at most once per distinct (type,id) -> tiny loops.
    for nd in nodes:
        if nd.get("Relation Name") == "release_title_cache":
            assert nd["Actual Loops"] <= 8, nd["Actual Loops"]
    jit = plan.get("JIT", {}).get("Timing", {}).get("Total", 0)
    print(
        "TITLE_SPARSE_MISS_NARROW_PLAN "
        + json.dumps(
            {"exec_ms": plan["Execution Time"], "jit_ms": jit, "cost": plan["Plan"]["Total Cost"]}
        )
    )
    assert ms < float(os.environ.get("TITLE_PERF_SPARSE_MS", "2000")), (
        f"wall {ms:.0f}ms; plan exec {plan['Execution Time']}ms of which JIT {jit}ms"
    )


def total_buffers(node):
    return node.get("Shared Hit Blocks", 0) + node.get("Shared Read Blocks", 0)


def test_exact_release_history_miss_uses_partial_index_not_history_scan(writer):
    """Structural baseline (independent measurement, 100k history, no index): Seq Scan, 100000
    rows removed, 1539 buffers, 8.44 ms. With ix_listens_release_mbid: bitmap scan, ~2 buffers,
    0.085 ms. Thresholds are structural (buffers) with a generous wall bound for CI. This proves
    application-cold (fresh process/cache), not physically cold storage."""
    n = 100_000
    seed_history(writer, n, 20_000)
    with writer.begin() as c:
        c.execute(text("UPDATE listens SET release_mbid=md5('rel-'||(id%5000))::uuid WHERE id%7=0"))
        c.execute(text("ANALYZE listens"))
    assert (
        explain(
            writer,
            "SELECT 1 FROM listens WHERE release_mbid = %(m)s::uuid "
            "AND nullif(btrim(release_name), '') IS NOT NULL",
            {"m": "00000000-0000-0000-0000-000000000000"},
        )
        is not None
    )
    plan = explain(
        writer,
        "SELECT 1 FROM listens WHERE release_mbid = %(m)s::uuid "
        "AND nullif(btrim(release_name), '') IS NOT NULL",
        {"m": "00000000-0000-0000-0000-000000000000"},
    )
    nodes = list(plan_nodes(plan["Plan"]))
    assert not [nd for nd in nodes if nd["Node Type"] == "Seq Scan"], plan["Plan"]
    assert any(nd.get("Index Name") == "ix_listens_release_mbid" for nd in nodes)
    assert (
        sum(
            total_buffers(nd)
            for nd in nodes
            if nd.get("Relation Name") == "listens" or "Index Name" in nd
        )
        <= 32
    )  # baseline without index: 1539
    assert plan["Execution Time"] < float(os.environ.get("TITLE_PERF_EXACT_MISS_MS", "100"))


def test_wide_cold_with_many_misses_baseline(writer, api):
    n = 100_000
    seed_history(writer, n, 20_000)
    with writer.begin() as c:
        # 2000 unnamed ids, no cache rows (all misses), spread across history
        c.execute(
            text(
                """
            INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,release_group_mbid)
            SELECT 'perf-miss', timestamptz '2020-01-01' + (g * interval '37 minutes'),
                   md5('miss-'||g)::uuid, 'Artist M', md5('missrg-'||(g % 2000))::uuid
            FROM generate_series(1,6000) g"""
            )
        )
        c.execute(text("ANALYZE listens"))
        set_generation(c)
    f = filters("0001-01-01", "9999-12-31", limit=100)
    q = SQLStatisticsQueries(api)  # cold: fresh cache
    t0 = time.perf_counter()
    try:
        seen = capture_album_sql(api, lambda: albums(q, f))
    except StatisticsUnavailable:
        pytest.fail(
            "wide cold album ranking exceeded the API statement_timeout (5s); "
            "check plan JIT time (cost-inflated JIT optimisation) first"
        )
    ms = (time.perf_counter() - t0) * 1000
    plan = explain(writer, *seen[0]) if seen else None
    print(
        "TITLE_WIDE_COLD_MISSES "
        + json.dumps(
            {
                "listens": n + 6000,
                "ms": round(ms, 1),
                "plan_ms": plan["Execution Time"] if plan else None,
            }
        )
    )
    assert seen, "album ranking must consult release_title_cache (T1 contract)"
    # Baseline: must finish inside the API statement_timeout (5s) with headroom.
    assert ms < float(os.environ.get("TITLE_PERF_COLD_WIDE_MS", "4000"))
    cache_nodes = [
        nd for nd in plan_nodes(plan["Plan"]) if nd.get("Relation Name") == "release_title_cache"
    ]
    for nd in cache_nodes:
        # dedup: never one probe per listen (106k) - at most per distinct id (<= ~23k x 2 types)
        assert nd["Actual Loops"] * max(nd["Actual Rows"], 1) < 60_000


# ---------------------------------------------------------------- review-driven EXPLAIN regressions
def executed_listens_nodes(plan):
    return [
        nd
        for nd in plan_nodes(plan["Plan"])
        if nd.get("Relation Name") == "listens" and nd.get("Actual Loops", 1) > 0
    ]


def album_plans(writer, api, f):
    q = SQLStatisticsQueries(api)
    seen = capture_album_sql(api, lambda: albums(q, f))
    assert seen, "album ranking must consult release_title_cache"
    return [explain(writer, *s) for s in seen]


@pytest.mark.parametrize("blank", ["   ", "\u00a0\u3000\u2003", "\u2028\u0085\u1680", ""])
def test_idless_blank_album_with_sparse_exact_miss_never_scans_all_history(writer, api, blank):
    """An ID-less row whose album folds to empty (ASCII/Unicode whitespace) is 'missing', not a
    named unmapped album: it must not enable the canonical all-history (artist, album) scan."""
    n = 60_000
    seed_history(writer, n, 3000)
    with writer.begin() as c:
        add_listens(
            c,
            [{"at": "2026-06-01T10:00:00Z", "rel": REL}] * 3  # sparse exact miss
            + [{"at": "2026-06-01T10:05:00Z", "album": blank}],  # ID-less, blank album
        )
        set_generation(c)
    for plan in album_plans(writer, api, filters("2026-06-01")):
        executed = executed_listens_nodes(plan)
        assert not [nd for nd in executed if nd["Node Type"] == "Seq Scan"], [
            (nd["Node Type"], nd["Actual Rows"]) for nd in executed
        ]
        rows = sum(nd["Actual Rows"] * nd["Actual Loops"] for nd in executed)
        assert rows < n * 0.05, rows


@pytest.mark.parametrize("conflict", [False, True], ids=["exact_hit", "exact_conflict"])
def test_exact_release_decision_never_probes_huge_group_history(writer, api, conflict):
    """Exact release evidence (a hit, or a conflict that drops the bucket) decides; release-group
    evidence must not be touched, however large that group's history is."""
    big = 40_000
    with writer.begin() as c:
        c.execute(
            text(
                """INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,
                   release_name,release_group_mbid)
                   SELECT 'big', timestamptz '2020-01-01' + g * interval '5 minutes',
                          md5('big-'||g)::uuid, 'A', 'Group name '||(g % 50), CAST(:rg AS uuid)
                   FROM generate_series(1,:n) g"""
            ),
            {"rg": RG, "n": big},
        )
        # exact release history under a different release id than the nameless window rows
        exact = [{"at": "2019-01-01T00:00:00Z", "album": "Exact A", "rel": REL}]
        if conflict:
            exact.append({"at": "2019-01-02T00:00:00Z", "album": "Exact B", "rel": REL})
        add_listens(c, exact)
        c.execute(
            text("UPDATE listens SET release_group_mbid=NULL WHERE release_mbid=CAST(:r AS uuid)"),
            {"r": REL},
        )
        c.execute(text("ANALYZE listens"))
    # window rows carry release REL + group RG and no name, so the bucket key is RG
    with writer.begin() as c:
        add_listens(c, [{"at": "2026-06-01T10:00:00Z", "rg": RG, "rel": REL}] * 3)
        set_generation(c)
    for plan in album_plans(writer, api, filters("2026-06-01")):
        for nd in executed_listens_nodes(plan):
            text_cond = " ".join(
                str(nd.get(k, "")) for k in ("Index Cond", "Recheck Cond", "Filter")
            )
            assert "release_group_mbid = " not in text_cond or nd.get("Actual Rows", 0) == 0, (
                nd["Node Type"],
                text_cond,
                nd["Actual Rows"],
            )
            assert nd["Node Type"] != "Seq Scan", text_cond
        rows = sum(nd["Actual Rows"] * nd["Actual Loops"] for nd in executed_listens_nodes(plan))
        assert rows < 1000, rows  # nowhere near the 40k group history
