"""Artwork resolution use case. Exact-ID chains ALWAYS run first and win:

album  : CAA image ListenBrainz already reported (exact caa_release_mbid + caa_id)
         -> Deezer album/upc:{barcode of representative release} -> Deezer URL-rel on that release
         -> CAA front of the key (release-group, or the release for release-keyed albums)
         -> CAA release-group front of the release's MB release-group relation -> miss
track  : LB-reported CAA image -> CAA front of the most-listened own release
         -> Deezer track/isrc:{isrc} album cover -> CAA front of releases MB lists for the
         recording (max 5) -> miss
artist : MB Deezer URL-rel -> Wikidata P2722 (via MB wikidata URL-rel) -> Deezer artist picture

Only when every exact step misses (or the entity has no MBID at all) does the Koito-style NAME
fallback run, Deezer search only, bounded (<=3 requests per entity), rejecting ambiguous and
conflicting candidates (domain/artwork/matching.py):
album  : title + each known artist (<=2) -> title alone (last resort; artists, if known, must
         still match). Album title only ever comes from the listened album name.
track  : track title + known artist -> that track's album cover (listened album title narrows
         candidates) -> album title + artist (never the track title as an album title) -> miss
artist : exact name, then MusicBrainz name/trusted aliases (MBID artists) -> miss

Transient failures never become misses. Deezer quota (code 4) stops the run without burning
the entity's attempts.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from music_dashboard.domain.artwork import matching
from music_dashboard.domain.artwork.entities import ArtworkOutcome, ArtworkWork
from music_dashboard.domain.artwork.errors import ArtworkQuotaError, ArtworkTransientError
from music_dashboard.domain.artwork.policies import ArtworkPolicy

CAA_BASE = "https://coverartarchive.org"


class ArtworkRepository(Protocol):
    def due(self, now: datetime, limit: int) -> list[ArtworkWork]: ...
    def attempts(self, work: ArtworkWork) -> int: ...
    def save(
        self, work: ArtworkWork, outcome: ArtworkOutcome, now: datetime, next_retry_at: datetime
    ) -> bool:
        """Persist; True when the visible (resolved) image_url changed."""
        ...

    def bump_generation(self) -> None: ...


class ReleaseLike(Protocol):
    @property
    def barcode(self) -> str | None: ...
    @property
    def deezer_album_id(self) -> str | None: ...
    @property
    def release_group_mbid(self) -> str | None: ...


class RecordingLike(Protocol):
    @property
    def isrcs(self) -> list[str]: ...
    @property
    def release_mbids(self) -> list[str]: ...


class ArtistLinksLike(Protocol):
    @property
    def deezer_artist_id(self) -> str | None: ...
    @property
    def wikidata_qid(self) -> str | None: ...
    @property
    def name(self) -> str | None: ...
    @property
    def aliases(self) -> tuple[str, ...]: ...


class MusicBrainzLookupsPort(Protocol):
    def release(self, mbid: str) -> ReleaseLike | None: ...
    def recording(self, mbid: str) -> RecordingLike: ...
    def artist_links(self, mbid: str) -> ArtistLinksLike: ...


class DeezerPort(Protocol):
    def album_by_upc(self, barcode: str) -> str | None: ...
    def album(self, deezer_id: str) -> str | None: ...
    def track_album_cover(self, isrc: str) -> str | None: ...
    def artist_picture(self, deezer_id: str) -> str | None: ...
    def search_artists(self, name: str) -> list[matching.ArtistCandidate]: ...
    def search_albums(
        self, title: str, artist: str | None = None
    ) -> list[matching.AlbumCandidate]: ...
    def search_tracks(self, title: str, artist: str) -> list[matching.TrackCandidate]: ...


class CoverArtPort(Protocol):
    def front_url(self, kind: str, mbid: str) -> str | None: ...
    def hint_url(self, release_mbid: str, caa_id: str) -> str | None: ...


class WikidataPort(Protocol):
    def deezer_artist_id(self, qid: str) -> str | None: ...


@dataclass
class QuotaGate:
    """Process-wide Deezer quota backoff (error code 4): doubles per strike, capped."""

    base_seconds: float = 10.0
    max_seconds: float = 900.0
    strikes: int = 0
    blocked_until: datetime | None = None

    def blocked(self, now: datetime) -> bool:
        return self.blocked_until is not None and now < self.blocked_until

    def trip(self, now: datetime) -> None:
        delay = min(self.max_seconds, self.base_seconds * 2**self.strikes)
        self.strikes = min(self.strikes + 1, 20)
        self.blocked_until = now + timedelta(seconds=delay)

    def clear(self) -> None:
        self.strikes = 0
        self.blocked_until = None


MAX_QUERY_ARTISTS = 2  # distinct known artist names tried per album/track search


def _distinct(names: tuple[str, ...], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = matching.fold(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out[:limit]


class ResolveArtwork:
    def __init__(
        self,
        repository: ArtworkRepository,
        musicbrainz: MusicBrainzLookupsPort,
        deezer: DeezerPort,
        caa: CoverArtPort,
        wikidata: WikidataPort,
        policy: ArtworkPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rand: Callable[[], float] = random.random,
        quota: QuotaGate | None = None,
    ) -> None:
        self.repository = repository
        self.mb = musicbrainz
        self.deezer = deezer
        self.caa = caa
        self.wikidata = wikidata
        self.policy = policy or ArtworkPolicy()
        self.clock = clock
        self.rand = rand
        self.quota = quota or QuotaGate()
        self._links_memo: tuple[str, ArtistLinksLike] | None = None

    # --- chains -------------------------------------------------------------------------
    def _album(self, work: ArtworkWork) -> tuple[str, str] | None:
        if work.caa_hint:
            hinted = self.caa.hint_url(*work.caa_hint)
            if hinted:
                return "caa", hinted
        release = work.representative_release_mbid
        info = self.mb.release(release) if release else None
        if info is not None:
            url = self.deezer.album_by_upc(info.barcode) if info.barcode else None
            if url is None and info.deezer_album_id:
                url = self.deezer.album(info.deezer_album_id)
            if url:
                return "deezer", url
        url = self.caa.front_url(work.album_kind, work.ref)
        if url is None and work.album_kind == "release" and info and info.release_group_mbid:
            url = self.caa.front_url("release-group", info.release_group_mbid)
        return ("caa", url) if url else None

    def _track(self, work: ArtworkWork) -> tuple[str, str] | None:
        if work.caa_hint:
            hinted = self.caa.hint_url(*work.caa_hint)
            if hinted:
                return "caa", hinted
        own = work.representative_release_mbid
        if own:
            url = self.caa.front_url("release", own)
            if url:
                return "caa", url
        recording = self.mb.recording(work.ref)
        for isrc in recording.isrcs:
            url = self.deezer.track_album_cover(isrc)
            if url:
                return "deezer", url
        for other in recording.release_mbids:
            if other == own:
                continue
            url = self.caa.front_url("release", other)
            if url:
                return "caa", url
        return None

    def _artist(self, work: ArtworkWork) -> tuple[str, str] | None:
        links = self._links(work)
        deezer_id = links.deezer_artist_id
        if deezer_id is None and links.wikidata_qid:
            deezer_id = self.wikidata.deezer_artist_id(links.wikidata_qid)
        url = self.deezer.artist_picture(deezer_id) if deezer_id else None
        return ("deezer", url) if url else None

    def _links(self, work: ArtworkWork) -> ArtistLinksLike:
        if self._links_memo is None or self._links_memo[0] != work.ref:
            self._links_memo = (work.ref, self.mb.artist_links(work.ref))
        return self._links_memo[1]

    # --- name fallbacks (Deezer search; only after the exact chain missed) ---------------------
    @staticmethod
    def _by_name(found: matching.Match | None) -> ArtworkOutcome | None:
        if found is None:
            return None
        return ArtworkOutcome("resolved", "deezer", found.image_url, None, found.kind, found.ref)

    def _album_by_name(
        self, title: str | None, artists: tuple[str, ...], *, title_only_ok: bool
    ) -> matching.Match | None:
        if not title or not matching.fold(title):
            return None
        known = _distinct(artists, MAX_QUERY_ARTISTS)
        for artist in known:
            found = matching.match_album(self.deezer.search_albums(title, artist), title, artists)
            if found:
                return found
        if known and not title_only_ok:
            return None
        return matching.match_album(self.deezer.search_albums(title), title, artists)

    def _album_name(self, work: ArtworkWork) -> ArtworkOutcome | None:
        names = work.names
        return self._by_name(self._album_by_name(names.title, names.artists, title_only_ok=True))

    def _track_name(self, work: ArtworkWork) -> ArtworkOutcome | None:
        names = work.names
        if not names.title or not matching.folded_set(names.artists):
            return None  # a track is never matched by title alone
        for artist in _distinct(names.artists, MAX_QUERY_ARTISTS):
            found = matching.match_track(
                self.deezer.search_tracks(names.title, artist),
                names.title,
                names.artists,
                names.release,
            )
            if found:
                return self._by_name(found)
        # Matched ALBUM cover: the listened album title (never the track title) plus artist.
        return self._by_name(self._album_by_name(names.release, names.artists, title_only_ok=False))

    def _artist_name(self, work: ArtworkWork) -> ArtworkOutcome | None:
        names = work.names
        aliases = list(names.aliases)
        if work.mbid is not None:
            links = self._links(work)
            aliases = [*aliases, *([links.name] if links.name else []), *links.aliases]
        wanted = [n for n in (names.title, *aliases) if n and matching.fold(n)]
        if not wanted:
            return None
        primary = matching.fold(names.title)
        pool: dict[str, matching.ArtistCandidate] = {}
        for name in _distinct(tuple(wanted), 3):  # listened name, then MB name/aliases
            pool.update({c.id: c for c in self.deezer.search_artists(name)})
            found = matching.match_artist(pool.values(), names.title, aliases)
            if found:
                return self._by_name(found)
            if primary and any(matching.fold(c.name) == primary for c in pool.values()):
                return None  # the real name is present but ambiguous: aliases must not rescue it
        return None

    def resolve(self, work: ArtworkWork) -> ArtworkOutcome:
        self._links_memo = None
        if work.mbid is not None:
            chain = {"album": self._album, "track": self._track, "artist": self._artist}[
                work.entity_type
            ]
            found = chain(work)
            if found is not None:
                source, url = found
                return ArtworkOutcome("resolved", source, url)  # type: ignore[arg-type]
        fallback = {
            "album": self._album_name,
            "track": self._track_name,
            "artist": self._artist_name,
        }[work.entity_type]
        return fallback(work) or ArtworkOutcome("miss")

    # --- run ----------------------------------------------------------------------------
    def run(self, limit: int = 20) -> dict[str, int]:
        if not 1 <= limit <= 1000:
            raise ValueError("Artwork batch limit must be 1..1000")
        stats = {"resolved": 0, "miss": 0, "transient": 0, "quota": 0}
        if self.quota.blocked(self.clock()):
            stats["quota"] = 1
            return stats
        changed = False
        for work in self.repository.due(self.clock(), limit):
            try:
                outcome = self.resolve(work)
            except ArtworkQuotaError:
                self.quota.trip(self.clock())
                stats["quota"] = 1
                break
            except ArtworkTransientError as error:
                attempts = self.repository.attempts(work) + 1
                outcome = ArtworkOutcome("transient", retry_after=error.retry_after)
                delay = self.policy.backoff(attempts, error.retry_after)
            else:
                delay = self.policy.ttl(outcome.status == "resolved", self.rand)
            now = self.clock()
            if self.repository.save(work, outcome, now, now + delay):
                changed = True
            stats[outcome.status] += 1
        else:
            self.quota.clear()
        if changed:
            self.repository.bump_generation()
        return stats
