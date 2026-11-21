"""Real PostgreSQL persistence and restart proof; all upstream clients are synthetic."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from music_dashboard.application.enrich_metadata import EnrichmentPolicy, EnrichMetadata
from music_dashboard.domain.metadata.entities import EnrichmentWork
from music_dashboard.domain.metadata.errors import TransientMetadataError
from music_dashboard.domain.metadata.policies import GenreVote, resolve_countries, resolve_genres
from music_dashboard.infrastructure.persistence.models import (
    ArtistCountry,
    DataGeneration,
    EnrichmentQueueItem,
    EntityGenre,
    Listen,
    ListenArtist,
    MetadataCache,
)
from music_dashboard.infrastructure.persistence.repositories.metadata import SqlMetadataRepository

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def uid(key):
    return str(uuid5(NAMESPACE_URL, "enrichment-test:" + key))


def add_listen(engine, key, *, recording=None, rg=None, artists=()):
    with Session(engine) as session, session.begin():
        listen = Listen(
            user_name="synthetic",
            listened_at=NOW,
            recording_msid=UUID(uid(key)),
            recording_mbid=UUID(recording) if recording else None,
            release_group_mbid=UUID(rg) if rg else None,
        )
        session.add(listen)
        session.flush()
        for position, artist in enumerate(artists):
            session.add(
                ListenArtist(
                    listen_id=listen.id,
                    position=position,
                    artist_name="Synthetic",
                    artist_mbid=UUID(artist),
                )
            )


class LB:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or []

    def recording_metadata(self, mbids):
        self.calls.append(mbids)
        return self.payload


class MB:
    def __init__(self, payload=None, *, fail=False):
        self.calls = []
        self.payload = payload or {}
        self.fail = fail

    def genres(self, kind, mbid):
        self.calls.append((kind, mbid))
        if self.fail:
            raise TransientMetadataError("synthetic transient", retry_after=90)
        return self.payload.get((kind, mbid), {"genres": []})


def test_frequency_queue_dedup_and_restart_no_requests(migrated_db):
    rec, artist, genre = uid("recording"), uid("artist"), uid("rock")
    for i in range(3):
        add_listen(migrated_db, str(i), recording=rec, artists=[artist, artist])
    low = uid("low")
    add_listen(migrated_db, "low", recording=low)
    lb = LB(
        [{"recording_mbid": rec, "genres": [{"genre_mbid": genre, "name": "Rock", "count": 5}]}]
    )
    mb = MB({("artist", artist): {"genres": [], "country": "JP"}})
    repo = SqlMetadataRepository(migrated_db)
    repo.refresh_queue(NOW)
    ready = repo.ready(NOW, 100)
    assert ready[0].priority == ready[1].priority == 3
    assert {row.entity_type: row.priority for row in ready if row.mbid == artist} == {"artist": 3}
    assert ready[-1].mbid == low
    assert EnrichMetadata(repo, lb, mb, clock=lambda: NOW).run() == 3
    assert ("recording", rec) not in mb.calls  # LB primary supplied recording genres
    first_calls = (len(lb.calls), len(mb.calls))
    restarted = EnrichMetadata(SqlMetadataRepository(migrated_db), lb, mb, clock=lambda: NOW)
    assert restarted.run() == 0
    assert (len(lb.calls), len(mb.calls)) == first_calls
    with Session(migrated_db) as session:
        assert session.scalar(select(ArtistCountry.country_code)) == "JP"
        assert session.scalar(select(EntityGenre.votes)) == 5
        assert session.scalar(select(func.count()).select_from(MetadataCache)) == 4
        generation = session.get(DataGeneration, 1).enrichment_generation
        assert generation == 2
    # Forced queue retry with fresh successful cache: no duplicate HTTP or generation bump.
    with migrated_db.begin() as connection:
        connection.execute(update(EnrichmentQueueItem).values(next_retry_at=NOW))
    assert restarted.run() == 3
    assert (len(lb.calls), len(mb.calls)) == first_calls
    with Session(migrated_db) as session:
        assert session.get(DataGeneration, 1).enrichment_generation == generation


def test_transient_retry_survives_restart_and_preserves_projection(migrated_db):
    artist, genre = uid("artist"), uid("rock")
    add_listen(migrated_db, "one", artists=[artist])
    repo = SqlMetadataRepository(migrated_db)
    mb = MB(
        {
            ("artist", artist): {
                "genres": [{"mbid": genre, "count": 1, "name": "Rock"}],
                "country": "US",
            }
        }
    )
    policy = EnrichmentPolicy(positive_ttl=timedelta(seconds=10))
    assert EnrichMetadata(repo, LB(), mb, policy, lambda: NOW).run() == 1
    mb.fail = True
    later = NOW + timedelta(seconds=11)
    assert EnrichMetadata(repo, LB(), mb, policy, lambda: later).run() == 0
    with Session(migrated_db) as session:
        cache = session.scalar(select(MetadataCache))
        assert cache.status == "transient_error"
        assert cache.next_retry_at == later + timedelta(seconds=90)
        assert cache.attempts == 1
        assert session.scalar(select(EntityGenre.votes)) == 1
        assert session.scalar(select(ArtistCountry.country_code)) == "US"
        generation = session.get(DataGeneration, 1).enrichment_generation
    count = len(mb.calls)
    assert (
        EnrichMetadata(
            SqlMetadataRepository(migrated_db),
            LB(),
            mb,
            policy,
            lambda: later + timedelta(seconds=89),
        ).run()
        == 0
    )
    assert len(mb.calls) == count
    mb.fail = False
    mb.payload = {}  # genuinely empty result now removes previous projection
    assert EnrichMetadata(repo, LB(), mb, policy, lambda: later + timedelta(seconds=90)).run() == 1
    with Session(migrated_db) as session:
        assert session.scalar(select(MetadataCache.status)) == "empty"
        assert session.scalar(select(func.count()).select_from(EntityGenre)) == 0
        assert session.scalar(select(func.count()).select_from(ArtistCountry)) == 0
        assert session.get(DataGeneration, 1).enrichment_generation == generation + 1


def test_saved_lookup_recovers_crash_before_projection(migrated_db):
    rec, genre = uid("recording"), uid("jazz")
    add_listen(migrated_db, "one", recording=rec)
    repo = SqlMetadataRepository(migrated_db)
    lb = LB(
        [{"recording_mbid": rec, "genres": [{"genre_mbid": genre, "name": "Jazz", "count": 0}]}]
    )

    class InterruptedRepository(SqlMetadataRepository):
        def complete(self, *args, **kwargs):
            raise InterruptedError("synthetic kill between cache commit and projection")

    import pytest

    with pytest.raises(InterruptedError):
        EnrichMetadata(InterruptedRepository(migrated_db), lb, MB(), clock=lambda: NOW).run()
    assert len(lb.calls) == 1
    assert EnrichMetadata(repo, lb, MB(), clock=lambda: NOW).run() == 1
    assert len(lb.calls) == 1
    with Session(migrated_db) as session:
        assert session.scalar(select(EntityGenre.votes)) == 0


def test_persisted_levels_feed_weight_invariants(migrated_db):
    r, rg, a, b, unknown = [uid(key) for key in ["r", "rg", "a", "b", "unknown"]]
    jazz, rock = uid("jazz"), uid("rock")
    repo = SqlMetadataRepository(migrated_db)
    repo.complete(
        EnrichmentWork("release_group", rg, 1),
        [GenreVote(jazz, "Jazz", 0)],
        None,
        NOW + timedelta(days=7),
        NOW,
    )
    repo.complete(
        EnrichmentWork("artist", a, 1),
        [GenreVote(rock, "Rock", 9)],
        "US",
        NOW + timedelta(days=7),
        NOW,
    )
    repo.complete(
        EnrichmentWork("artist", b, 1),
        [GenreVote(jazz, "Jazz", 1)],
        "US",
        NOW + timedelta(days=7),
        NOW,
    )
    with Session(migrated_db) as session:
        rows = session.execute(select(EntityGenre)).scalars().all()
        genres = {}
        for row in rows:
            genres.setdefault((row.entity_type, str(row.entity_mbid)), []).append(
                GenreVote(str(row.genre_mbid), "", row.votes)
            )
        countries = {
            str(row.artist_mbid): row.country_code for row in session.scalars(select(ArtistCountry))
        }
    weights = resolve_genres(
        genres.get(("recording", r), []),
        genres[("release_group", rg)],
        [a, a, b, unknown],
        {a: genres[("artist", a)], b: genres[("artist", b)]},
    )
    assert weights == {jazz: 1}
    assert sum(weights.values()) == 1
    assert resolve_countries([a, a, b, unknown], countries) == {"US": 1}


def test_source_cooldown_covers_new_entities_after_restart(migrated_db):
    first, second = uid("first-artist"), uid("second-artist")
    add_listen(migrated_db, "first", artists=[first])
    mb = MB(fail=True)
    assert (
        EnrichMetadata(SqlMetadataRepository(migrated_db), LB(), mb, clock=lambda: NOW).run() == 0
    )
    add_listen(migrated_db, "second", artists=[second])
    later = NOW + timedelta(seconds=89)
    # A brand-new repository/process still sees the other entity's source cooldown.
    repo = SqlMetadataRepository(migrated_db)
    assert repo.source_retry_at("musicbrainz") == NOW + timedelta(seconds=90)
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: later).run() == 0
    assert len(mb.calls) == 1
    mb.fail = False
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: NOW + timedelta(seconds=90)).run() == 2
    assert len(mb.calls) == 3


def test_positive_rate_headers_cooldown_is_durable(migrated_db):
    first, second = uid("first-artist"), uid("second-artist")
    add_listen(migrated_db, "first", artists=[first])
    mb = MB({("artist", first): {"genres": [], "country": "US", "_retry_after": 75}})
    repo = SqlMetadataRepository(migrated_db)
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: NOW).run() == 1
    assert repo.source_retry_at("musicbrainz") == NOW + timedelta(seconds=75)
    add_listen(migrated_db, "second", artists=[second])
    assert (
        EnrichMetadata(
            SqlMetadataRepository(migrated_db), LB(), mb, clock=lambda: NOW + timedelta(seconds=74)
        ).run()
        == 0
    )
    assert len(mb.calls) == 1


def test_preferred_lb_tags_wake_completed_projection(migrated_db):
    from music_dashboard.domain.metadata.entities import CachedLookup

    artist, rock, jazz = uid("artist"), uid("rock"), uid("jazz")
    add_listen(migrated_db, "one", artists=[artist])
    repo = SqlMetadataRepository(migrated_db)
    mb = MB(
        {
            ("artist", artist): {
                "genres": [{"mbid": rock, "name": "Rock", "count": 2}],
                "country": "JP",
            }
        }
    )
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: NOW).run() == 1
    later = NOW + timedelta(seconds=1)
    repo.save_lookup(
        CachedLookup(
            "artist",
            artist,
            "listenbrainz",
            "positive",
            {"genres": [{"genre_mbid": jazz, "name": "Jazz", "count": 0}]},
            later,
            later + timedelta(days=7),
        )
    )
    assert repo.ready(later, 100) == [EnrichmentWork("artist", artist, 1)]
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: later).run() == 1
    assert len(mb.calls) == 1  # country is still fresh; reprojection does not require HTTP
    with Session(migrated_db) as session:
        assert str(session.scalar(select(EntityGenre.genre_mbid))) == jazz
        assert session.scalar(select(ArtistCountry.country_code)) == "JP"
        assert session.get(DataGeneration, 1).enrichment_generation == 2


def test_terminal_failure_does_not_starve_other_entities(migrated_db):
    from music_dashboard.domain.metadata.errors import MetadataPermanentError

    bad, good = uid("bad-artist"), uid("good-artist")
    add_listen(migrated_db, "one", artists=[bad])
    add_listen(migrated_db, "two", artists=[bad])
    add_listen(migrated_db, "three", artists=[good])

    class BadRequest(MB):
        def genres(self, kind, mbid):
            if mbid == bad:
                self.calls.append((kind, mbid))
                raise MetadataPermanentError("synthetic bad request")
            return super().genres(kind, mbid)

    mb = BadRequest()
    repo = SqlMetadataRepository(migrated_db)
    assert EnrichMetadata(repo, LB(), mb, clock=lambda: NOW).run(limit=1) == 0
    assert (
        EnrichMetadata(SqlMetadataRepository(migrated_db), LB(), mb, clock=lambda: NOW).run(limit=1)
        == 1
    )
    assert mb.calls == [("artist", bad), ("artist", good)]
    assert repo.ready(NOW, 100) == []
    with Session(migrated_db) as session:
        rejected = session.scalar(
            select(EnrichmentQueueItem).where(EnrichmentQueueItem.entity_mbid == UUID(bad))
        )
        assert rejected.status == "failed" and rejected.next_retry_at is None
        assert session.scalar(select(func.count()).select_from(MetadataCache)) == 1


def test_job_composition_and_advisory_lock(migrated_db, monkeypatch):
    from sqlalchemy import text

    from music_dashboard.application.jobs.enrichment import run_once
    from music_dashboard.infrastructure.listenbrainz import metadata as lb_module
    from music_dashboard.infrastructure.musicbrainz import client as mb_module
    from music_dashboard.infrastructure.persistence.repositories.metadata import ENRICHMENT_LOCK

    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.setenv("MUSIC_DATABASE_URL", migrated_db.url.render_as_string(hide_password=False))
    monkeypatch.setenv(
        "MUSIC_METADATA_USER_AGENT", "music-dashboard/test (operator@example.invalid)"
    )
    artist = uid("artist")
    add_listen(migrated_db, "one", artists=[artist])
    lb, mb = LB(), MB({("artist", artist): {"genres": [], "country": "RO"}})
    monkeypatch.setattr(lb_module, "ListenBrainzMetadataClient", lambda config: lb)
    monkeypatch.setattr(mb_module, "MusicBrainzClient", lambda config: mb)
    with migrated_db.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ENRICHMENT_LOCK})
        connection.commit()
        try:
            assert run_once() == 0
            assert mb.calls == []
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ENRICHMENT_LOCK})
            connection.commit()
    assert run_once() == 1
    assert mb.calls == [("artist", artist)]
    assert run_once() == 0
