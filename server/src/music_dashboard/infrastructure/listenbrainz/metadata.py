"""ListenBrainz batch recording-metadata adapter (primary enrichment path).

Shape checked against upstream GitHub sources (not live API calls):
listenbrainz/testdata/mb_metadata_cache_example.json and
listenbrainz/webserver/views/metadata_api.py, metabrainz/listenbrainz-server.
POST inc is whitespace-separated (parse_incs); '+' is GET URL encoding only.
Artist tags are flat with artist_mbid; release_group_mbid belongs to release.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
)
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    HttpTransport,
    parse_retry_after,
    urllib_transport,
)

MAX_BATCH = 1000
# Upstream parse_incs() does str.split() (whitespace), so spaces are required in the POST body.
INC = "artist tag release recording release_group"
DEFAULT_RETRY_AFTER = 60.0


@dataclass(frozen=True)
class ListenBrainzMetadataConfig:
    user_agent: str
    base_url: str = "https://api.listenbrainz.org"
    token: str | None = None
    timeout: float = 60.0

    def __repr__(self) -> str:
        return "ListenBrainzMetadataConfig(...)"


class ListenBrainzMetadataClient:
    def __init__(
        self, config: ListenBrainzMetadataConfig, transport: HttpTransport = urllib_transport
    ) -> None:
        self._config = config
        self._transport = transport

    def recording_metadata(self, mbids: Sequence[str]) -> list[dict[str, Any]]:
        """POST /1/metadata/recording/. Returns one normalized dict per found recording
        (see normalize_entry); unknown mbids are omitted.
        Raises ValueError if >1000 mbids."""
        if not mbids:
            return []
        if len(mbids) > MAX_BATCH:
            raise ValueError(f"at most {MAX_BATCH} mbids per batch")
        headers = {
            "User-Agent": self._config.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._config.token:
            headers["Authorization"] = f"Token {self._config.token}"
        body = json.dumps({"recording_mbids": list(mbids), "inc": INC}).encode()
        request = HttpRequest(
            "POST",
            f"{self._config.base_url}/1/metadata/recording/",
            headers,
            body,
            self._config.timeout,
        )
        resp = self._transport(request)
        if resp.status == 429 or resp.status >= 500:
            wait = parse_retry_after(resp.headers)
            raise MetadataTransientError(
                f"listenbrainz http {resp.status}",
                wait if wait is not None else DEFAULT_RETRY_AFTER,
            )
        if resp.status != 200:
            raise MetadataPermanentError(f"listenbrainz http {resp.status}")
        try:
            data = json.loads(resp.body)
        except ValueError:
            raise MetadataTransientError("listenbrainz invalid json", DEFAULT_RETRY_AFTER) from None
        if not isinstance(data, dict):
            raise MetadataTransientError("listenbrainz unexpected body", DEFAULT_RETRY_AFTER)
        return [
            normalize_entry(mbid, meta) for mbid, meta in data.items() if isinstance(meta, dict)
        ]


def _count(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetadataTransientError("listenbrainz malformed genre count", DEFAULT_RETRY_AFTER)
    return value


def recognized_genres(tags: Any) -> list[dict[str, Any]]:
    """Keep only LB tag entries with genre_mbid (rejects free tags like 'seen-live').
    Returns [{"genre_mbid","name","count"}]; aggregates duplicate genre_mbid votes.
    Malformed list/entry/count types raise MetadataTransientError."""
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise MetadataTransientError("listenbrainz malformed tag list", DEFAULT_RETRY_AFTER)
    out: dict[str, dict[str, Any]] = {}
    for t in tags:
        if not isinstance(t, dict):
            raise MetadataTransientError("listenbrainz malformed tag", DEFAULT_RETRY_AFTER)
        gid = t.get("genre_mbid")
        if not gid:
            continue
        entry = out.setdefault(gid, {"genre_mbid": gid, "name": t.get("tag"), "count": 0})
        entry["count"] += _count(t.get("count"))
    return list(out.values())


def normalize_entry(mbid: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Normalized shape (all genre lists are [{"genre_mbid","name","count"}]):
    recording_mbid, genres (recording-level), release_group_genres, artist_genres
    (per artist: [{"artist_mbid", "genres": [...]}]), artists [{"artist_mbid","name"}],
    release_group_mbid (or None), metadata (raw LB entry)."""
    tag: dict[str, Any] = _d(meta.get("tag"))
    by_artist: dict[str, list[dict[str, Any]]] = {}
    artist_tags = tag.get("artist")
    if artist_tags is not None and not isinstance(artist_tags, list):
        raise MetadataTransientError("listenbrainz malformed tag list", DEFAULT_RETRY_AFTER)
    for t in artist_tags or []:
        if isinstance(t, dict) and t.get("artist_mbid"):
            by_artist.setdefault(t["artist_mbid"], []).append(t)
    artist_block: dict[str, Any] = _d(meta.get("artist"))
    raw_artists = artist_block.get("artists")
    if raw_artists is not None and not isinstance(raw_artists, list):
        raise MetadataTransientError("listenbrainz malformed artists", DEFAULT_RETRY_AFTER)
    artists = [
        {"artist_mbid": a.get("artist_mbid"), "name": a.get("name")}
        for a in raw_artists or []
        if isinstance(a, dict) and a.get("artist_mbid")
    ]
    # Upstream cache: release group mbid lives at release.release_group_mbid.
    rg_mbid = _d(meta.get("release")).get("release_group_mbid")
    return {
        "recording_mbid": mbid,
        "genres": recognized_genres(tag.get("recording")),
        "release_group_genres": recognized_genres(tag.get("release_group")),
        "artist_genres": [
            {"artist_mbid": k, "genres": recognized_genres(v)} for k, v in by_artist.items()
        ],
        "artists": artists,
        "release_group_mbid": rg_mbid,
        "metadata": meta,
    }


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}
