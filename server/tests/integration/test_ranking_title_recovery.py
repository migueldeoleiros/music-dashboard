"""Rankings-only album/track title recovery. Synthetic data, disposable PostgreSQL only."""

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.seed import seed_fixtures
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries

REL1 = "ec30b077-3364-41de-84c1-fbd6612e1e1e"
REL2 = "22222222-0000-4000-8000-000000000002"
REL3 = "33333333-0000-4000-8000-000000000003"
RG1 = "98fb14cc-9134-479b-baa6-18a881e18dca"
RG2 = "44444444-0000-4000-8000-000000000004"
REC1 = "55555555-0000-4000-8000-000000000005"
REC2 = "66666666-0000-4000-8000-000000000006"
DAY = "2026-03-10"
OLD = "2025-01-01"


@pytest.fixture
def env(migrated_db, api_url):
    seed_fixtures(migrated_db)
    with migrated_db.begin() as c:
        # Until the worker migration lands in this tree: same shape/grants as the agreed contract.
        c.execute(
            text("""CREATE TABLE IF NOT EXISTS release_title_cache(
              entity_type text NOT NULL CHECK (entity_type IN ('release','release_group')),
              entity_mbid uuid NOT NULL, title text NOT NULL,
              PRIMARY KEY (entity_type, entity_mbid))""")
        )
        c.execute(text("GRANT SELECT ON release_title_cache TO music_api"))
        c.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_listens_release_mbid ON listens(release_mbid) "
                "WHERE release_mbid IS NOT NULL"
            )
        )
        c.execute(text("DELETE FROM listens"))
        c.execute(text("DELETE FROM release_title_cache"))
        c.execute(text("DELETE FROM artwork"))
    engine = create_engine(api_url)
    yield SQLStatisticsQueries(engine), migrated_db
    engine.dispose()


def listen(c, day=DAY, artist="tripleS", album=None, rg=None, rel=None, track=None, rec=None):
    c.execute(
        text("""INSERT INTO listens(user_name,listened_at,recording_msid,artist_name,release_name,
              track_name,recording_mbid,release_group_mbid,release_mbid)
              VALUES ('t',CAST(:at AS timestamptz),CAST(:m AS uuid),:artist,:album,:track,
              CAST(:rec AS uuid),CAST(:rg AS uuid),CAST(:rel AS uuid))"""),
        {
            "at": f"{day}T12:00:00Z",
            "m": str(uuid.uuid4()),
            "artist": artist,
            "album": album,
            "track": track,
            "rec": rec,
            "rg": rg,
            "rel": rel,
        },
    )


def bump(c):
    c.execute(text("UPDATE data_generation SET ingestion_generation=ingestion_generation+1"))


def cache(c, kind, mbid, title):
    c.execute(
        text("INSERT INTO release_title_cache VALUES (:k, CAST(:m AS uuid), :t)"),
        {"k": kind, "m": mbid, "t": title},
    )


def rank(q, family, limit=10, start=DAY, end=None):
    f = StatisticsFilter(date.fromisoformat(start), date.fromisoformat(end or start), "UTC", limit)
    r = q.query(family, f)
    return {i["key"]: i for i in r["items"]}, r


def test_blank_album_recovers_from_exact_release_history_counts_unchanged(env):
    q, w = env
    with w.begin() as c:
        for _ in range(3):  # window: blank album, release + group present (tripleS case)
            listen(c, rg=RG1, rel=REL1)
        listen(c, day=OLD, album="<LOVE&POP> pt.1", rg=RG1, rel=REL1)
        bump(c)
    items, r = rank(q, "rankings/albums")
    assert list(items) == [RG1]
    assert items[RG1]["label"] == "<LOVE&POP> pt.1"
    assert items[RG1]["metadata_state"] == "resolved"
    assert items[RG1]["listens"] == 3 and items[RG1]["share"] == 1.0
    assert r["coverage"]["albums"]["total_listens"] == 3  # coverage untouched


def test_exact_release_conflict_blocks_group_fallback_and_hides(env):
    q, w = env
    with w.begin() as c:
        listen(c, rg=RG1, rel=REL1)
        listen(c, day=OLD, album="Original", rg=RG1, rel=REL1)
        listen(c, day=OLD, album="Deluxe", rg=RG1, rel=REL1)  # release-level conflict
        listen(c, day=OLD, album="Group Title", rg=RG1)  # group evidence must NOT rescue
        bump(c)
    items, r = rank(q, "rankings/albums")
    assert items == {}
    assert r["coverage"]["albums"]["total_listens"] == 1


def test_release_group_used_only_when_release_evidence_absent(env):
    q, w = env
    with w.begin() as c:
        listen(c, rg=RG1, rel=REL1)  # REL1 has no named history at all
        listen(c, day=OLD, album="Group Title", rg=RG1, rel=REL2)
        listen(c, day=OLD, album="GROUP TITLE ", rg=RG1)  # folded-equal spelling: consistent
        listen(c, rg=RG2)  # group-only blank, conflicting group history
        listen(c, day=OLD, album="A", rg=RG2)
        listen(c, day=OLD, album="B", rg=RG2)
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert set(items) == {RG1}
    assert items[RG1]["label"] in {"GROUP TITLE", "Group Title"}


def test_cache_precedence_history_first_and_release_before_group(env):
    q, w = env
    with w.begin() as c:
        listen(c, rg=RG1, rel=REL1)  # release cache must beat less-specific group history
        listen(c, day=OLD, album="Group Label", rg=RG1)
        cache(c, "release", REL1, "Exact Release")
        listen(c, rg=RG2, rel=REL2)  # stored release history beats a different cache title
        listen(c, day=OLD, album="Stored", rel=REL2)
        cache(c, "release", REL2, "Cache Title")
        listen(c, rel=REL3)  # release-only key; group cache can't apply, release cache does
        cache(c, "release", REL3, "Only Cache")
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert items[RG1]["label"] == "Exact Release"
    assert items[RG2]["label"] == "Stored"
    assert items[REL3]["label"] == "Only Cache"


def test_group_cache_fallback_and_cache_never_overrides_explicit(env):
    q, w = env
    with w.begin() as c:
        listen(c, rg=RG1, rel=REL1)
        cache(c, "release_group", RG1, "Group Cache")
        listen(c, album="Explicit", rg=RG2)
        cache(c, "release_group", RG2, "Cache Must Lose")
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert items[RG1]["label"] == "Group Cache"
    assert items[RG2]["label"] == "Explicit"


def test_hiding_all_unresolved_highest_unresolved_and_mixed(env):
    q, w = env
    with w.begin() as c:
        for _ in range(5):  # highest-count album is unresolved
            listen(c, rg=RG1)
        for _ in range(2):
            listen(c, album="Named Two", rg=RG2)
        listen(c, album="Named One", rel=REL2)
        listen(c, album="  ", artist="X")  # blank, unmapped
        listen(c, album=None, artist="Y")
        bump(c)
    items, r = rank(q, "rankings/albums", limit=1)
    assert [i["label"] for i in items.values()] == ["Named Two"]  # unresolved never takes rank 1
    assert items[RG2]["share"] == pytest.approx(2 / 3)  # denominator: named groups (3), pre-limit
    assert r["coverage"]["albums"]["total_listens"] == 10  # overall total unchanged
    items, _ = rank(q, "rankings/albums", limit=10)
    assert sum(i["listens"] for i in items.values()) == 3
    assert sum(i["share"] for i in items.values()) == pytest.approx(1.0)
    # All unresolved -> empty ranking.
    with w.begin() as c:
        c.execute(
            text("DELETE FROM listens WHERE release_name IS NOT NULL AND btrim(release_name)<>''")
        )
        bump(c)
    items, r = rank(q, "rankings/albums")
    assert items == {} and r["coverage"]["albums"]["total_listens"] == 7


def test_explicit_label_and_ties_preserved_in_mixed_bucket(env):
    q, w = env
    with w.begin() as c:
        listen(c, album="Zeta", rg=RG1)
        listen(c, album="Alpha", rg=RG1)
        listen(c, rg=RG1)  # blank sibling adopts an explicit label, state resolved
        listen(c, album="Same", rg=RG2)
        listen(c, album="Same", rel=REL2)  # same title, other id
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert items[RG1]["label"] == "Alpha" and items[RG1]["listens"] == 3
    assert items[RG1]["metadata_state"] == "resolved"
    # same (artist, title) under two ids: one merged key (the smallest id)
    assert list(items)[1:] == [min(RG2, REL2)] and items[min(RG2, REL2)]["listens"] == 2


def test_track_title_recovered_from_consistent_recording_history_only(env):
    q, w = env
    with w.begin() as c:
        listen(c, rec=REC1, track=None)
        listen(c, rec=REC1, track=" ")
        listen(c, day=OLD, rec=REC1, track="Baby Flower")
        listen(c, day=OLD, rec=REC1, track="BABY FLOWER ")  # folded-equal: still consistent
        listen(c, rec=REC2)
        listen(c, day=OLD, rec=REC2, track="One")
        listen(c, day=OLD, rec=REC2, track="Two")  # conflict -> unchanged
        listen(c, track=None)  # no MBID: never guessed
        bump(c)
    items, _ = rank(q, "rankings/tracks")
    assert items[REC1]["label"] == "BABY FLOWER"  # min() of trimmed spellings, deterministic
    assert items[REC1]["metadata_state"] == "resolved"
    assert items[REC2]["label"] == "[Missing title]"
    assert items[REC2]["metadata_state"] == "missing"
    assert items[REC1]["listens"] == 2


def test_track_recovery_resolves_badge(env):
    q, w = env
    with w.begin() as c:
        listen(c, rec=REC1)
        listen(c, rec=REC1, track="Baby Flower")  # same bucket in window, explicit sibling
        listen(c, rec=REC2)
        listen(c, day=OLD, rec=REC2, track="History Title")
        bump(c)
    items, _ = rank(q, "rankings/tracks")
    assert items[REC1]["label"] == "Baby Flower" and items[REC1]["metadata_state"] == "resolved"
    assert items[REC2]["label"] == "History Title" and items[REC2]["metadata_state"] == "resolved"


def plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def explain_album_ranking(writer):
    from music_dashboard.infrastructure.persistence import statistics_queries as sq

    params = SQLStatisticsQueries._params(
        StatisticsFilter(date.fromisoformat(DAY), date.fromisoformat(DAY), "UTC", 10)
    )
    sql = (
        sq.COMMON
        + sq.ALBUM_RANKING
        + ", totals AS ("
        + sq.totals_sql("albums", "album_ranking_values")
        + ") SELECT * FROM totals"
    )
    with writer.begin() as c:
        c.execute(text("SET LOCAL enable_seqscan=off"))
        c.execute(text("SET LOCAL jit=off"))  # same as the endpoint for these families
        raw = c.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params).scalar_one()
    return list(plan_nodes(raw[0]["Plan"]))


def test_group_probe_skipped_after_exact_release_hit_or_conflict(env):
    q, w = env
    with w.begin() as c:
        for _ in range(3):
            listen(c, day=OLD, album="Hot Group", rg=RG1)  # expensive repeated group
        listen(c, rg=RG1, rel=REL1)  # exact hit -> never needs the group
        listen(c, day=OLD, album="Exact", rg=RG1, rel=REL1)
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert items[RG1]["label"] == "Exact"
    nodes = explain_album_ranking(w)
    probes = [n for n in nodes if n.get("Index Name") == "ix_listens_release_group_mbid"]
    assert all(n["Actual Loops"] == 0 for n in probes), "group index probed despite exact hit"
    # Conflict at release level also blocks any group probe (and hides the album).
    with w.begin() as c:
        listen(c, day=OLD, album="Other", rg=RG1, rel=REL1)
        bump(c)
    items, _ = rank(q, "rankings/albums")
    assert items == {}
    probes = [
        n
        for n in explain_album_ranking(w)
        if n.get("Index Name") == "ix_listens_release_group_mbid"
    ]
    assert all(n["Actual Loops"] == 0 for n in probes)


def test_unnamed_idless_listen_never_triggers_canonical_history_scan(env):
    q, w = env
    with w.begin() as c:
        listen(c, day=OLD, album="Some Album", rg=RG2, artist="Someone")  # history exists
        listen(c, rg=RG1, rel=REL1)  # sparse exact miss (no evidence anywhere)
        for blank in ("", "  ", "\u3000\u00a0"):  # ID-less, unusable album names
            listen(c, album=blank, artist="Someone")
        listen(c, album="Some Album", artist="  ")  # usable album but blank artist
        bump(c)
    nodes = explain_album_ranking(w)
    # Everything under the album_history CTE must be gated off (never executed).
    ah = [n for n in nodes if n.get("Subplan Name") == "CTE album_history"]
    assert ah, "album_history CTE not found"
    under = [m for n in ah for m in plan_nodes(n) if m.get("Relation Name") == "listens"]
    assert under and all(m["Actual Loops"] == 0 for m in under)
    items, _ = rank(q, "rankings/albums")
    # Only the named (blank-artist) name-keyed album survives; blank names stay hidden.
    assert [i["label"] for i in items.values()] == ["Some Album"]
    # Control: one usable ID-less listen (album AND artist) does enable the history lookup.
    with w.begin() as c:
        listen(c, album="Some Album", artist="Someone")
        bump(c)
    ah = [n for n in explain_album_ranking(w) if n.get("Subplan Name") == "CTE album_history"]
    under = [m for n in ah for m in plan_nodes(n) if m.get("Relation Name") == "listens"]
    assert any(m["Actual Loops"] > 0 for m in under)
    items, _ = rank(q, "rankings/albums")
    assert RG2 in items and items[RG2]["label"] == "Some Album"  # canonical merge via history
