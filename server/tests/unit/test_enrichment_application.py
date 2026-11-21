from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from music_dashboard.application.enrich_metadata import (
    EnrichmentPolicy,
    EnrichMetadata,
    recognized_votes,
)
from music_dashboard.domain.metadata.entities import CachedLookup, EnrichmentWork
from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
)
from music_dashboard.domain.metadata.policies import (
    GenreVote,
    genre_weights,
    resolve_countries,
    resolve_genres,
    valid_country,
)

REC = "11111111-1111-1111-1111-111111111111"
REC2 = "11111111-1111-1111-1111-222222222222"
RG = "22222222-2222-2222-2222-222222222222"
A1 = "33333333-3333-3333-3333-333333333331"
A2 = "33333333-3333-3333-3333-333333333332"
G1 = "44444444-4444-4444-4444-444444444441"
G2 = "44444444-4444-4444-4444-444444444442"
G3 = "44444444-4444-4444-4444-444444444443"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def v(mbid: str, count: int) -> GenreVote:
    return GenreVote(mbid, mbid, count)


# ---------- domain policies ----------


def test_precedence_first_level_wins_no_mixing() -> None:
    result = resolve_genres([v(G1, 1)], [v(G2, 5)], [A1], {A1: [v(G3, 5)]})
    assert result == {G1: 1.0}
    assert resolve_genres([], [v(G2, 5)], [A1], {A1: [v(G3, 5)]}) == {G2: 1.0}
    assert resolve_genres([], [], [A1], {A1: [v(G3, 5)]}) == {G3: 1.0}
    assert resolve_genres([], [], [A1], {}) == {}


def test_vote_rules() -> None:
    assert genre_weights([v(G1, 3), v(G2, 1)]) == {G1: 0.75, G2: 0.25}
    # zero/negative discarded when positives exist
    assert genre_weights([v(G1, 2), v(G2, 0), v(G3, -4)]) == {G1: 1.0}
    # recognized but no positive votes -> equal shares
    assert genre_weights([v(G1, 0), v(G2, -1)]) == {G1: 0.5, G2: 0.5}
    # duplicate identity aggregated
    assert genre_weights([v(G1, 1), v(G1, 1), v(G2, 2)]) == {G1: 0.5, G2: 0.5}
    assert genre_weights([]) == {}


def test_multiartist_dedupe_normalize_skip_unresolved() -> None:
    votes = {A1: [v(G1, 9), v(G2, 1)], A2: [v(G2, 4)]}
    r = resolve_genres([], [], [A1, A1, A2, "unresolved"], votes)
    assert r[G1] == pytest.approx(0.45)
    assert r[G2] == pytest.approx(0.55)
    assert sum(r.values()) == pytest.approx(1.0)
    # duplicated credit does not double weight
    single = resolve_genres([], [], [A1, A1], votes)
    assert single == {G1: pytest.approx(0.9), G2: pytest.approx(0.1)}


def test_country_rules() -> None:
    assert resolve_countries([A1, A2], {A1: "GB", A2: "FR"}) == {"GB": 0.5, "FR": 0.5}
    assert resolve_countries([A1, A2], {A1: "GB", A2: "GB"}) == {"GB": 1.0}
    assert resolve_countries([A1, A2], {A1: "GB", A2: None}) == {"GB": 1.0}  # partial
    assert resolve_countries([A1, A1], {A1: "GB"}) == {"GB": 1.0}
    assert resolve_countries([A1, A2], {A1: "XW", A2: "XE"}) == {}
    for bad in ("gb", "ZZ", "GBR", "", None, 5):
        assert valid_country(bad) is None
    assert valid_country("GB") == "GB"
    assert resolve_countries([A1, A2], {A1: "XW", A2: "DE"}) == {"DE": 1.0}


def test_recognized_votes_free_tags_and_invalid_uuid() -> None:
    lb = {
        "genres": [
            {"genre_mbid": G1, "name": "rock", "count": 2},
            {"tag": "seen-live", "count": 9},
            {"genre_mbid": "not-a-uuid", "name": "bad", "count": 3},
            {"genre_mbid": G1, "name": "rock", "count": 1},
            {"genre_mbid": G2, "count": True},
            "junk",
        ]
    }
    votes = {x.mbid: x.count for x in recognized_votes(lb)}
    assert votes == {G1: 3, G2: 0}
    assert recognized_votes({"genres": "x"}) == []
    mb = {
        "genres": [{"mbid": G1, "name": "a", "count": 1}, {"id": G2, "name": "b"}, {"mbid": "bad"}]
    }
    assert {x.mbid for x in recognized_votes(mb, musicbrainz=True)} == {G1, G2}
    # LB parser must not accept MB-style id keys
    assert recognized_votes({"genres": [{"mbid": G1}]}) == []


# ---------- application with fakes ----------


class FakeRepo:
    def __init__(self, work: list[EnrichmentWork]) -> None:
        self.queue = {(w.entity_type, w.mbid): w for w in work}
        self.lookups: dict[tuple[str, str, str], CachedLookup] = {}
        self.completed: dict[tuple[str, str], tuple[list[GenreVote], str | None, datetime]] = {}
        self.retries: dict[tuple[str, str], datetime] = {}
        self.fail_complete = False
        self.rejected: set[tuple[str, str]] = set()

    def refresh_queue(self, now: datetime) -> None:
        pass

    def ready(self, now: datetime, limit: int) -> list[EnrichmentWork]:
        out = [
            w
            for k, w in self.queue.items()
            if k not in self.completed
            and k not in self.rejected
            and self.retries.get(k, now) <= now
        ]
        return sorted(out, key=lambda w: -w.priority)[:limit]

    def source_retry_at(self, source: Any) -> datetime | None:
        due = []
        for lk in self.lookups.values():
            if lk.source != source:
                continue
            if lk.status == "transient_error":
                due.append(lk.next_retry_at)
            marker = lk.payload.get("_source_retry_at")
            if isinstance(marker, str):
                due.append(datetime.fromisoformat(marker))
        return max(due) if due else None

    def reject(self, work: EnrichmentWork, now: datetime) -> None:
        self.rejected.add((work.entity_type, work.mbid))

    def cached(self, entity_type: Any, mbid: str, source: Any) -> CachedLookup | None:
        return self.lookups.get((entity_type, mbid, source))

    def save_lookup(self, lookup: CachedLookup) -> None:
        self.lookups[(lookup.entity_type, lookup.mbid, lookup.source)] = lookup

    def complete(
        self,
        work: EnrichmentWork,
        votes: list[GenreVote],
        country: str | None,
        next_retry_at: datetime,
        now: datetime,
    ) -> None:
        if self.fail_complete:
            raise RuntimeError("crash")
        self.completed[(work.entity_type, work.mbid)] = (votes, country, next_retry_at)

    def retry(self, work: EnrichmentWork, next_retry_at: datetime, now: datetime) -> None:
        self.retries[(work.entity_type, work.mbid)] = next_retry_at


class FakeLB:
    def __init__(
        self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[list[str]] = []

    def recording_metadata(self, mbids: list[str]) -> list[dict[str, Any]]:
        self.calls.append(list(mbids))
        if self.error:
            raise self.error
        return self.rows


class FakeMB:
    def __init__(self, data: dict[tuple[str, str], Any] | None = None) -> None:
        self.data = data or {}
        self.calls: list[tuple[str, str]] = []

    def genres(self, entity_type: Any, mbid: str) -> dict[str, Any]:
        self.calls.append((entity_type, mbid))
        r = self.data.get((entity_type, mbid), {"genres": []})
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def make(
    repo: FakeRepo,
    lb: FakeLB,
    mb: FakeMB,
    clock: Clock,
    policy: EnrichmentPolicy | None = None,
) -> EnrichMetadata:
    return EnrichMetadata(repo, lb, mb, policy or EnrichmentPolicy(), clock)  # type: ignore[arg-type]


def lb_row(mbid: str = REC, **extra: Any) -> dict[str, Any]:
    return {
        "recording_mbid": mbid,
        "genres": [{"genre_mbid": G1, "name": "rock", "count": 2}],
        **extra,
    }


def test_lb_success_skips_mb_recording() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    lb, mb, clock = FakeLB([lb_row()]), FakeMB(), Clock()
    assert make(repo, lb, mb, clock).run() == 1
    assert mb.calls == []
    assert repo.completed[("recording", REC)][0] == [GenreVote(G1, "rock", 2)]
    assert lb.calls == [[REC]]


def test_recording_without_genres_falls_back_to_mb() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    lb = FakeLB([{"recording_mbid": REC, "genres": []}])
    mb = FakeMB({("recording", REC): {"genres": [{"mbid": G2, "name": "pop", "count": 1}]}})
    assert make(repo, lb, mb, Clock()).run() == 1
    assert mb.calls == [("recording", REC)]
    assert [x.mbid for x in repo.completed[("recording", REC)][0]] == [G2]


def test_recording_absent_from_lb_falls_back_and_empty_is_cached() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    app = make(repo, FakeLB([]), FakeMB(), clock)
    assert app.run() == 1
    assert repo.completed[("recording", REC)][0] == []
    assert repo.lookups[("recording", REC, "musicbrainz")].status == "empty"


def test_lb_release_group_and_artist_levels_cached_from_recording_row() -> None:
    row = lb_row(
        release_group_mbid=RG,
        release_group_genres=[{"genre_mbid": G2, "name": "pop", "count": 1}],
        artist_genres=[
            {"artist_mbid": A1, "genres": [{"genre_mbid": G3, "name": "jazz", "count": 1}]}
        ],
    )
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    make(repo, FakeLB([row]), FakeMB(), Clock()).run()
    assert repo.lookups[("release_group", RG, "listenbrainz")].status == "positive"
    assert repo.lookups[("artist", A1, "listenbrainz")].status == "positive"


def test_artist_country_fetched_independently_of_lb_genres() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
    repo.save_lookup(
        CachedLookup(
            "artist",
            A1,
            "listenbrainz",
            "positive",
            {"genres": [{"genre_mbid": G1, "count": 1}]},
            T0,
            T0 + timedelta(days=30),
        )
    )
    mb = FakeMB({("artist", A1): {"genres": [], "country": "GB"}})
    make(repo, FakeLB(), mb, Clock()).run()
    assert mb.calls == [("artist", A1)]
    votes, country, _ = repo.completed[("artist", A1)]
    assert country == "GB" and [x.mbid for x in votes] == [G1]


def test_artist_country_invalid_or_pseudo_is_none() -> None:
    for code in ("XW", "XE", "zz", None):
        repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
        mb = FakeMB({("artist", A1): {"genres": [], "country": code}})
        make(repo, FakeLB(), mb, Clock()).run()
        assert repo.completed[("artist", A1)][1] is None


def test_transient_source_failure_never_empty_and_exponential_durable() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
    clock = Clock()
    mb = FakeMB({("artist", A1): MetadataTransientError("x", None)})
    app = make(repo, FakeLB(), mb, clock)
    delays = []
    for _ in range(3):
        assert app.run() == 0
        lk = repo.lookups[("artist", A1, "musicbrainz")]
        assert lk.status == "transient_error"
        delays.append((lk.next_retry_at - clock.now).total_seconds())
        assert A1 not in [k[1] for k in repo.completed]
        clock.now = lk.next_retry_at
    assert delays == [30, 60, 120]


def test_retry_after_respected_and_not_called_before_due() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
    clock = Clock()
    mb = FakeMB({("artist", A1): MetadataTransientError("x", 600)})
    app = make(repo, FakeLB(), mb, clock)
    app.run()
    assert (repo.retries[("artist", A1)] - clock.now).total_seconds() == 600
    clock.now += timedelta(seconds=100)
    assert app.run() == 0
    assert len(mb.calls) == 1  # not ready yet


def test_lb_batch_transient_marks_retry_not_empty() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    mb = FakeMB()
    lb = FakeLB(error=MetadataTransientError("x", 10))
    assert make(repo, lb, mb, Clock()).run() == 0
    assert repo.lookups[("recording", REC, "listenbrainz")].status == "transient_error"
    assert ("recording", REC) not in repo.completed
    assert mb.calls == []


def test_restart_reuses_cache_saved_before_crash() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    lb, mb = FakeLB([lb_row()]), FakeMB()
    repo.fail_complete = True
    with pytest.raises(RuntimeError):
        make(repo, lb, mb, clock).run()
    assert ("recording", REC, "listenbrainz") in repo.lookups
    repo.fail_complete = False
    lb2 = FakeLB([lb_row()])
    assert make(repo, lb2, mb, clock).run() == 1  # simulated restart, new instance
    assert lb2.calls == [] and mb.calls == []
    assert ("recording", REC) in repo.completed


def test_completed_work_not_refetched_after_restart() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    make(repo, FakeLB([lb_row()]), FakeMB(), clock).run()
    lb2 = FakeLB([lb_row()])
    assert make(repo, lb2, FakeMB(), clock).run() == 0
    assert lb2.calls == []


def test_ttl_positive_and_empty_expiry() -> None:
    policy = EnrichmentPolicy(empty_ttl=timedelta(days=7), positive_ttl=timedelta(days=30))
    repo = FakeRepo([EnrichmentWork("recording", REC, 1), EnrichmentWork("recording", REC2, 1)])
    clock = Clock()
    lb = FakeLB([lb_row(REC), {"recording_mbid": REC2, "genres": []}])
    make(repo, lb, FakeMB(), clock, policy).run()
    pos = repo.lookups[("recording", REC, "listenbrainz")]
    emp = repo.lookups[("recording", REC2, "listenbrainz")]
    assert pos.next_retry_at - T0 == timedelta(days=30)
    assert emp.next_retry_at - T0 == timedelta(days=7)
    # after expiry the cache is stale and refetched
    clock.now = T0 + timedelta(days=8)
    repo.completed.clear()
    lb2 = FakeLB([lb_row(REC), {"recording_mbid": REC2, "genres": []}])
    make(repo, lb2, FakeMB(), clock, policy).run()
    assert lb2.calls == [[REC2]]  # only empty one expired
    clock.now = T0 + timedelta(days=31)
    repo.completed.clear()
    lb3 = FakeLB([lb_row(REC), {"recording_mbid": REC2, "genres": []}])
    make(repo, lb3, FakeMB(), clock, policy).run()
    assert sorted(lb3.calls[0]) == sorted([REC, REC2])


def test_run_limits() -> None:
    repo = FakeRepo([])
    app = make(repo, FakeLB(), FakeMB(), Clock())
    for bad in (0, -1, 1001):
        with pytest.raises(ValueError):
            app.run(bad)
    work = [EnrichmentWork("artist", f"33333333-3333-3333-3333-{i:012d}", i) for i in range(5)]
    repo = FakeRepo(work)
    mb = FakeMB()
    assert make(repo, FakeLB(), mb, Clock()).run(limit=2) == 2
    assert len(mb.calls) == 2
    assert [c[1] for c in mb.calls] == [work[4].mbid, work[3].mbid]  # priority order


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        EnrichmentPolicy(retry_base_seconds=0)
    with pytest.raises(ValueError):
        EnrichmentPolicy(empty_ttl=timedelta(0))


# ---------- review regression tests ----------


class CrashOnceRepo(FakeRepo):
    def __init__(self, work: list[EnrichmentWork]) -> None:
        super().__init__(work)
        self.crashed = False

    def save_lookup(self, lookup: CachedLookup) -> None:
        if lookup.entity_type == "release_group" and not self.crashed:
            self.crashed = True
            raise InterruptedError
        super().save_lookup(lookup)


def test_crash_after_recording_cache_replays_children_without_lb_repeat() -> None:
    row = lb_row(
        release_group_mbid=RG,
        release_group_genres=[{"genre_mbid": G2, "name": "pop", "count": 1}],
        artist_genres=[
            {"artist_mbid": A1, "genres": [{"genre_mbid": G3, "name": "jazz", "count": 1}]}
        ],
    )
    repo = CrashOnceRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    lb1 = FakeLB([row])
    with pytest.raises(InterruptedError):
        make(repo, lb1, FakeMB(), clock).run()
    assert ("recording", REC, "listenbrainz") in repo.lookups
    assert ("release_group", RG, "listenbrainz") not in repo.lookups
    lb2 = FakeLB([row])
    make(repo, lb2, FakeMB(), clock).run()  # restart
    assert lb2.calls == []
    assert repo.lookups[("release_group", RG, "listenbrainz")].status == "positive"
    assert repo.lookups[("artist", A1, "listenbrainz")].status == "positive"


def test_lb_cooldown_blocks_new_recordings_including_new_instance() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    lb = FakeLB(error=MetadataTransientError("x", 600))
    make(repo, lb, FakeMB(), clock).run()
    assert len(lb.calls) == 1
    repo.queue[("recording", REC2)] = EnrichmentWork("recording", REC2, 99)
    clock.now += timedelta(seconds=100)
    for app in (make(repo, lb, FakeMB(), clock), make(repo, lb, FakeMB(), clock)):
        assert app.run() == 0
    assert len(lb.calls) == 1
    assert ("recording", REC2) not in repo.completed
    clock.now = T0 + timedelta(seconds=601)
    lb.error = None
    lb.rows = [lb_row(REC), lb_row(REC2)]
    assert make(repo, lb, FakeMB(), clock).run() == 2
    assert len(lb.calls) == 2


def test_mb_cooldown_blocks_new_artists_including_new_instance() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
    clock = Clock()
    mb = FakeMB({("artist", A1): MetadataTransientError("x", 600)})
    make(repo, FakeLB(), mb, clock).run()
    repo.queue[("artist", A2)] = EnrichmentWork("artist", A2, 99)
    clock.now += timedelta(seconds=100)
    for app in (make(repo, FakeLB(), mb, clock), make(repo, FakeLB(), mb, clock)):
        assert app.run() == 0
    assert mb.calls == [("artist", A1)]
    assert ("artist", A2) not in repo.completed


def test_entity_permanent_error_rejects_and_low_priority_progresses() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 10), EnrichmentWork("artist", A2, 1)])
    clock = Clock()
    mb = FakeMB(
        {
            ("artist", A1): MetadataPermanentError("bad"),
            ("artist", A2): {"genres": [], "country": "FR"},
        }
    )
    app = make(repo, FakeLB(), mb, clock)
    assert app.run(limit=1) == 0
    assert ("artist", A1) in repo.rejected and ("artist", A1) not in repo.completed
    assert app.run(limit=1) == 1
    assert ("artist", A2) in repo.completed
    assert mb.calls == [("artist", A1), ("artist", A2)]
    app.run()
    assert mb.calls.count(("artist", A1)) == 1  # never ready again


def test_source_wide_permanent_error_is_durable_cooldown_not_terminal() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 10), EnrichmentWork("artist", A2, 1)])
    clock = Clock()
    mb = FakeMB({("artist", A1): MetadataPermanentError("auth", source_wide=True)})
    app = make(repo, FakeLB(), mb, clock)
    assert app.run() == 0
    assert not repo.rejected
    assert repo.lookups[("artist", A1, "musicbrainz")].status == "transient_error"
    assert repo.source_retry_at("musicbrainz") is not None
    clock.now += timedelta(seconds=5)
    assert make(repo, FakeLB(), mb, clock).run() == 0
    assert mb.calls == [("artist", A1)]  # cooldown blocks A2 and retry of A1
    mb.data = {("artist", A1): {"genres": [], "country": "GB"}, ("artist", A2): {"genres": []}}
    clock.now = repo.source_retry_at("musicbrainz") + timedelta(seconds=1)  # type: ignore[operator]
    repo.retries.clear()
    assert make(repo, FakeLB(), mb, clock).run() == 2


# ---------- delta review regressions ----------


def rg_row(mbid: str, genre: str, **extra: Any) -> dict[str, Any]:
    return {
        "recording_mbid": mbid,
        "genres": [],
        "release_group_mbid": RG,
        "release_group_genres": [{"genre_mbid": genre, "name": genre, "count": 1}],
        **extra,
    }


def seed_recording(repo: FakeRepo, mbid: str, row: dict[str, Any], fetched: datetime) -> None:
    repo.save_lookup(
        CachedLookup(
            "recording", mbid, "listenbrainz", "empty", row, fetched, fetched + timedelta(days=30)
        )
    )


def test_stale_recording_does_not_overwrite_newer_shared_child_cache() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    clock = Clock()
    clock.now = T0 + timedelta(days=1)
    make(repo, FakeLB([rg_row(REC, G2)]), FakeMB(), clock).run()  # newer B
    child = repo.lookups[("release_group", RG, "listenbrainz")]
    assert child.fetched_at == clock.now
    # older recording cache (fetched T0) replayed afterwards must not win
    seed_recording(repo, REC2, rg_row(REC2, G1), T0)
    repo.queue[("recording", REC2)] = EnrichmentWork("recording", REC2, 1)
    make(repo, FakeLB(), FakeMB(), clock).run()
    assert repo.lookups[("release_group", RG, "listenbrainz")] == child


def test_equal_fetched_child_tie_is_deterministic_regardless_of_order() -> None:
    results = []
    for first, second in ((REC, REC2), (REC2, REC)):
        repo = FakeRepo(
            [EnrichmentWork("recording", first, 2), EnrichmentWork("recording", second, 1)]
        )
        seed_recording(repo, REC, rg_row(REC, G1), T0)
        seed_recording(repo, REC2, rg_row(REC2, G2), T0)
        make(repo, FakeLB(), FakeMB(), Clock()).run()
        results.append(repo.lookups[("release_group", RG, "listenbrainz")].payload)
    assert results[0] == results[1]


def test_child_expiry_uses_positive_ttl_even_if_recording_is_empty() -> None:
    repo = FakeRepo([EnrichmentWork("recording", REC, 1)])
    policy = EnrichmentPolicy(empty_ttl=timedelta(days=7), positive_ttl=timedelta(days=30))
    clock = Clock()
    make(repo, FakeLB([rg_row(REC, G2)]), FakeMB(), clock, policy).run()
    rec = repo.lookups[("recording", REC, "listenbrainz")]
    child = repo.lookups[("release_group", RG, "listenbrainz")]
    assert rec.status == "empty" and rec.next_retry_at - T0 == timedelta(days=7)
    assert child.status == "positive" and child.next_retry_at - T0 == timedelta(days=30)


class ExpiringCooldownRepo(FakeRepo):
    """Advance clock past cooldown on 2nd cached() call (after _batch) for watched recording."""

    def __init__(self, work: list[EnrichmentWork], clock: Clock, watch: str) -> None:
        super().__init__(work)
        self.clock = clock
        self.watch = watch
        self.calls = 0
        self.armed = False

    def cached(self, entity_type: Any, mbid: str, source: Any) -> CachedLookup | None:
        if self.armed and (entity_type, mbid, source) == ("recording", self.watch, "listenbrainz"):
            self.calls += 1
            if self.calls == 2:
                self.clock.now = (self.source_retry_at("listenbrainz") or self.clock.now) + (
                    timedelta(seconds=1)
                )
        return super().cached(entity_type, mbid, source)


def test_cooldown_expiring_between_batch_and_lookup_does_not_crash() -> None:
    clock = Clock()
    repo = ExpiringCooldownRepo([EnrichmentWork("recording", REC, 1)], clock, REC2)
    lb = FakeLB(error=MetadataTransientError("x", 600))
    make(repo, lb, FakeMB(), clock).run()
    assert len(lb.calls) == 1
    repo.queue[("recording", REC2)] = EnrichmentWork("recording", REC2, 99)
    clock.now += timedelta(seconds=10)
    repo.armed = True
    assert make(repo, lb, FakeMB(), clock).run() == 0  # no RuntimeError
    assert repo.calls >= 2  # clock really crossed the cooldown mid-run
    assert len(lb.calls) == 1
    assert ("recording", REC2) not in repo.completed
    assert ("recording", REC2) in repo.retries


def test_positive_mb_retry_after_cooldown_across_instances() -> None:
    repo = FakeRepo([EnrichmentWork("artist", A1, 1)])
    clock = Clock()
    mb = FakeMB({("artist", A1): {"genres": [], "country": "GB", "_retry_after": 75}})
    assert make(repo, FakeLB(), mb, clock).run() == 1
    stored = repo.lookups[("artist", A1, "musicbrainz")]
    assert stored.status == "positive" and "_retry_after" not in stored.payload
    assert repo.source_retry_at("musicbrainz") == T0 + timedelta(seconds=75)
    repo.queue[("artist", A2)] = EnrichmentWork("artist", A2, 5)
    clock.now += timedelta(seconds=30)
    assert make(repo, FakeLB(), mb, clock).run() == 0
    assert mb.calls == [("artist", A1)]
    clock.now = T0 + timedelta(seconds=76)
    repo.retries.clear()
    mb.data[("artist", A2)] = {"genres": [], "country": "FR"}
    assert make(repo, FakeLB(), mb, clock).run() == 1
    assert mb.calls == [("artist", A1), ("artist", A2)]
