"""Release/release-group title cache on a disposable Postgres (synthetic upstream only)."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from music_dashboard.application.resolve_titles import ResolveTitles, TitlePolicy
from music_dashboard.domain.metadata.errors import MetadataTransientError
from music_dashboard.infrastructure.persistence.repositories.title_cache import (
    SqlTitleCacheRepository,
)

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def uid(k):
    return str(uuid5(NAMESPACE_URL, "title-test:" + k))


def listen(c, key, *, rel=None, rg=None, name=None):
    c.execute(
        text(
            "INSERT INTO listens (user_name, listened_at, recording_msid, release_mbid,"
            " release_group_mbid, release_name) VALUES ('u', :at, CAST(:m AS uuid),"
            " CAST(:rel AS uuid), CAST(:rg AS uuid), :n)"
        ),
        {
            "at": NOW + timedelta(seconds=hash(key) % 100000),
            "m": uid("msid" + key),
            "rel": rel,
            "rg": rg,
            "n": name,
        },
    )


def gens(c):
    return c.execute(
        text(
            "SELECT ingestion_generation, enrichment_generation, last_updated FROM data_generation"
        )
    ).first()


class Client:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def title(self, kind, mbid, timeout=None):
        self.calls.append((kind, mbid, timeout))
        a = self.answers.get((kind, mbid))
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def repo(migrated_db, admin_url):
    eng = create_engine(admin_url)
    yield SqlTitleCacheRepository(eng)
    eng.dispose()


def test_candidates_skip_stored_titles_dedup_and_bump_only_visible(migrated_db, repo):
    rg1, rg2, rel1, rel2, rel3 = (uid(x) for x in ("rg1", "rg2", "rel1", "rel2", "rel3"))
    with migrated_db.connect() as c:
        for i in range(3):
            listen(c, f"a{i}", rg=rg1, rel=rel1)  # no stored title
        listen(c, "b", rg=rg2, name="Stored")  # stored title wins -> no lookup
        listen(c, "c", rel=rel2)  # release-only
        listen(c, "d", rel=rel3, name="Stored")
        c.execute(
            text(
                "INSERT INTO data_generation (id, ingestion_generation) VALUES (1, 5)"
                " ON CONFLICT (id) DO UPDATE SET ingestion_generation = 5"
            )
        )
        before = gens(c)
    cands = repo.candidates(NOW, 10)
    # exact releases are candidates even when the listen also has a release group
    assert {(t, m) for t, m, _ in cands} == {
        ("release_group", rg1),
        ("release", rel1),
        ("release", rel2),
    }
    client = Client(
        {
            ("release_group", rg1): {"title": "RG One", "release_group_mbid": None},
            ("release", rel1): {"title": "Rel One", "release_group_mbid": rg1},
            ("release", rel2): {"title": "Rel Two", "release_group_mbid": uid("g")},
        }
    )
    stats = ResolveTitles(repo, client, TitlePolicy(), clock=lambda: NOW).run()
    assert stats["published"] == 3 and len(client.calls) == 3
    with migrated_db.connect() as c:
        after = gens(c)
        assert after.enrichment_generation == before.enrichment_generation + 3
        assert after.ingestion_generation == 5 and after.last_updated == before.last_updated
        assert (
            c.execute(text("SELECT count(*) FROM listens WHERE release_name IS NULL")).scalar() == 4
        )
    # Same title again: row refreshed but no bump.
    assert not repo.save_resolved("release_group", rg1, "RG One", None, NOW, NOW)
    assert repo.save_resolved("release_group", rg1, "RG One (new)", None, NOW, NOW)
    # miss keeps the resolved title, persists retry/attempts, never bumps.
    repo.save_miss("release_group", rg1, "transient", NOW + timedelta(hours=1), NOW, 3)
    with migrated_db.connect() as c:
        row = c.execute(
            text(
                "SELECT t.title, l.status, l.attempts FROM release_title_cache t JOIN"
                " release_title_lookup l USING (entity_type, entity_mbid)"
                " WHERE entity_mbid = CAST(:m AS uuid)"
            ),
            {"m": rg1},
        ).first()
        assert tuple(row) == ("RG One (new)", "transient", 3)
        assert gens(c).enrichment_generation == before.enrichment_generation + 4
    # Cached rows are not candidates until due.
    assert repo.candidates(NOW, 10) == []


def test_transient_cooldown_across_runs_and_budget(migrated_db, repo):
    ids = [uid(f"r{i}") for i in range(4)]
    with migrated_db.connect() as c:
        for i, m in enumerate(ids):
            listen(c, str(i), rg=m)
    err = MetadataTransientError("rl", retry_after=600)
    client = Client({("release_group", m): err for m in ids})
    clock = [NOW]

    def run():
        return ResolveTitles(
            repo, client, TitlePolicy(max_requests=3), clock=lambda: clock[0]
        ).run()

    assert run()["transient"] == 1 and len(client.calls) == 1  # stops at first transient
    assert run()["requests"] == 0 and len(client.calls) == 1  # cooldown across runs
    clock[0] = NOW + timedelta(seconds=601)
    client.answers = {
        ("release_group", m): {"title": f"T{i}", "release_group_mbid": None}
        for i, m in enumerate(ids)
    }
    s = run()
    assert s["requests"] == 3 and len(client.calls) == 4  # budget caps; timeout passed
    assert all(t is not None for _, _, t in client.calls[1:])


def test_not_found_negative_cache_and_api_grants(migrated_db, repo, api_url):
    m = uid("nf")
    with migrated_db.connect() as c:
        listen(c, "x", rel=m)
    client = Client({})
    ResolveTitles(repo, client, TitlePolicy(), clock=lambda: NOW).run()
    ResolveTitles(repo, client, TitlePolicy(), clock=lambda: NOW).run()
    assert len(client.calls) == 1
    api = create_engine(api_url)
    with api.connect() as c:
        assert (
            c.execute(text("SELECT entity_type, entity_mbid, title FROM release_title_cache")).all()
            == []
        )
    for col in ("status",):
        with api.connect() as c, pytest.raises(DBAPIError):
            c.execute(text(f"SELECT {col} FROM release_title_cache"))
    with api.connect() as c, pytest.raises(DBAPIError):
        c.execute(text("SELECT count(*) FROM release_title_lookup"))
    with api.connect() as c, pytest.raises(DBAPIError):
        c.execute(
            text(
                "INSERT INTO release_title_cache (entity_type, entity_mbid, title)"
                " VALUES ('release', gen_random_uuid(), 'x')"
            )
        )
    api.dispose()
    with migrated_db.connect() as c, pytest.raises(IntegrityError):
        c.execute(
            text(
                "INSERT INTO release_title_cache (entity_type, entity_mbid, title)"
                " VALUES ('release', gen_random_uuid(), '  ')"
            )
        )


def test_concurrent_publication_bumps_exactly_once_per_change(migrated_db, repo):
    m = uid("race")
    with migrated_db.connect() as c:
        listen(c, "r", rg=m)
    results, barrier = [], threading.Barrier(8)

    def work(i):
        barrier.wait()
        results.append(repo.save_resolved("release_group", m, "Same", None, NOW, NOW))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1
    with migrated_db.connect() as c:
        assert gens(c).enrichment_generation == 1


def test_warming_concurrent_with_refresh_row_and_ingest_generation(migrated_db, repo):
    """Title publication runs while refresh admission and ingestion bumps happen."""
    ids = [uid(f"w{i}") for i in range(6)]
    with migrated_db.connect() as c:
        for i, m in enumerate(ids):
            listen(c, f"w{i}", rg=m)
    client = Client(
        {
            ("release_group", m): {"title": f"W{i}", "release_group_mbid": None}
            for i, m in enumerate(ids)
        }
    )
    errors = []

    def warm():
        try:
            ResolveTitles(repo, client, TitlePolicy(), clock=lambda: NOW).run()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def refresh_and_ingest():
        try:
            for _ in range(10):
                with migrated_db.begin() as c:
                    c.execute(text("UPDATE refresh_state SET detail = 'x_y' WHERE singleton"))
                    c.execute(
                        text(
                            "INSERT INTO data_generation (id, ingestion_generation) VALUES (1, 1)"
                            " ON CONFLICT (id) DO UPDATE SET"
                            " ingestion_generation = data_generation.ingestion_generation + 1"
                        )
                    )
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=warm), threading.Thread(target=refresh_and_ingest)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    with migrated_db.connect() as c:
        g = gens(c)
        assert g.enrichment_generation == 6 and g.ingestion_generation == 10
        assert c.execute(text("SELECT count(*) FROM release_title_cache")).scalar() == 6
    assert UUID(ids[0])


def test_unicode_blank_names_are_candidates_like_ranking(migrated_db, repo):
    blanks = ["\t", "\u00a0", "\u3000", " \u00a0\t\u3000 ", "\u2003"]
    ids = [uid(f"blank{i}") for i in range(len(blanks))]
    stored = uid("stored")
    with migrated_db.connect() as c:
        for i, (m, b) in enumerate(zip(ids, blanks, strict=True)):
            listen(c, f"blank{i}", rg=m, name=b)
        listen(c, "stored", rg=stored, name="\u00a0Real\u3000")
    got = {m for _, m, _ in repo.candidates(NOW, 50)}
    assert got == set(ids)


def test_transient_after_resolved_keeps_title_and_source_cooldown(migrated_db, repo):
    a, b = uid("cd-a"), uid("cd-b")
    with migrated_db.connect() as c:
        listen(c, "cda", rg=a)
        listen(c, "cdb", rg=b)
    clock = [NOW]
    client = Client({("release_group", a): {"title": "Kept", "release_group_mbid": None}})
    client.answers[("release_group", b)] = {"title": "B", "release_group_mbid": None}
    ResolveTitles(repo, client, TitlePolicy(), clock=lambda: clock[0]).run()
    assert repo.source_retry_at() is None
    # Refresh of A fails transiently after it was resolved.
    clock[0] = NOW + timedelta(days=31)
    client.answers = {("release_group", a): MetadataTransientError("x", retry_after=600)}
    s = ResolveTitles(repo, client, TitlePolicy(), clock=lambda: clock[0]).run()
    assert s["transient"] == 1
    assert repo.source_retry_at() == clock[0] + timedelta(seconds=600)
    with migrated_db.connect() as c:
        assert (
            c.execute(
                text("SELECT title FROM release_title_cache WHERE entity_mbid = CAST(:m AS uuid)"),
                {"m": a},
            ).scalar()
            == "Kept"
        )
    # A fresh run inside the cooldown makes no request.
    n = len(client.calls)
    clock[0] += timedelta(seconds=10)
    assert ResolveTitles(repo, client, TitlePolicy(), clock=lambda: clock[0]).run()["requests"] == 0
    assert len(client.calls) == n
    # After the cooldown it retries.
    clock[0] += timedelta(seconds=601)
    client.answers = {("release_group", a): {"title": "Kept2", "release_group_mbid": None}}
    assert ResolveTitles(repo, client, TitlePolicy(), clock=lambda: clock[0]).run()["resolved"] == 1
    assert repo.source_retry_at() is None


def test_huge_retry_after_clamped_in_schedule(migrated_db, repo):
    m = uid("huge")
    with migrated_db.connect() as c:
        listen(c, "huge", rg=m)
    client = Client({("release_group", m): MetadataTransientError("x", retry_after=1e300)})
    ResolveTitles(repo, client, TitlePolicy(), clock=lambda: NOW).run()
    assert repo.source_retry_at() == NOW + timedelta(days=1)


def test_both_headers_persist_longest_cooldown(migrated_db, repo):
    from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
    from music_dashboard.infrastructure.musicbrainz.title_lookup import MusicBrainzTitleClient
    from music_dashboard.infrastructure.musicbrainz.transport import HttpResponse, RateLimiter

    m = uid("both")
    with migrated_db.connect() as c:
        listen(c, "both", rg=m)
    headers = {"retry-after": "30", "x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "300"}
    tc = MusicBrainzTitleClient(
        MusicBrainzConfig(user_agent="t/1 (mailto:a@b.org)"),
        lambda req: HttpResponse(429, headers, b""),
        RateLimiter(0, sleep=lambda s: None),
    )
    clock = [NOW]
    pol = TitlePolicy(retry_base_seconds=1)
    ResolveTitles(repo, tc, pol, clock=lambda: clock[0]).run()
    assert repo.source_retry_at() == NOW + timedelta(seconds=300)
    # a fresh run (new repo/client objects) still honours the cooldown
    fresh = SqlTitleCacheRepository(repo.engine)
    clock[0] = NOW + timedelta(seconds=100)
    assert ResolveTitles(fresh, tc, pol, clock=lambda: clock[0]).run()["requests"] == 0
