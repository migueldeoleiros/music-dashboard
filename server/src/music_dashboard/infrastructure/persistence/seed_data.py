"""Synthetic fixture data (no network, no tokens). Deterministic UUIDs via uuid5."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

_NS = uuid.UUID("6f1f3c0e-0000-4000-8000-00000000d00d")
FIXTURE_USER = "fixture_user"


def uid(key: str) -> uuid.UUID:
    return uuid.uuid5(_NS, key)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


GENRES = {k: k.title() for k in ("rock", "pop", "electronic", "jazz", "folk")}

# artist key -> (name, country or None). XW/XE are non-country codes; None => no row.
ARTISTS = {
    "jp": ("Aoi Tanaka", "JP"),
    "us": ("Blue Harbor", "US"),
    "ro": ("Corina Lupu", "RO"),
    "xw": ("Global Collective", "XW"),
    "xe": ("Euro Ensemble", "XE"),
    "nocountry": ("Nameless Duo", None),
}

# (entity_type, key, {genre: votes}) ; absent entities simply have no rows.
ENTITY_GENRES = [
    ("recording", "r_rec", {"rock": 6, "pop": 2, "folk": -1}),  # recording-level wins
    ("release_group", "g_rg", {"jazz": 4, "folk": 1}),  # release-group fallback
    ("artist", "jp", {"electronic": 3}),  # artist fallback (multi-credit)
    ("artist", "us", {"pop": 1, "rock": 1}),
    ("artist", "ro", {"folk": 2}),
    ("recording", "r_zero", {"jazz": 0, "folk": 0}),  # recognized, no positive votes
]

# at (ISO), track, [(artist key|None, name)], release|None, recording key|None, rg key|None
_L: list[dict[str, Any]] = []


def _add(
    at: str,
    track: str,
    artists: list[tuple[str | None, str]],
    release: str | None,
    rec: str | None,
    rg: str | None,
    tag: str,
) -> None:
    _L.append(
        dict(
            key=f"{tag}:{at}", at=at, track=track, artists=artists, release=release, rec=rec, rg=rg
        )
    )


US, JP, RO = ("us", "Blue Harbor"), ("jp", "Aoi Tanaka"), ("ro", "Corina Lupu")
# genre fallback cases
_add("2026-01-05T10:00:00Z", "Recording Level", [US], "Album A", "r_rec", "g_rec", "rec-genre")
_add("2026-01-05T10:10:00Z", "RG Level", [RO], "Album B", "r_rg", "g_rg", "rg-genre")
_add(
    "2026-01-05T10:20:00Z",
    "Artist Level Duo",
    [JP, US, JP],  # repeated MBID must be deduplicated by later weighting policies
    "Album C",
    "r_art",
    "g_art",
    "artist-genre",
)
_add("2026-01-05T10:30:00Z", "Zero Votes", [RO], "Album D", "r_zero", "g_zero", "zero-votes")
# unmapped (no MBIDs at all) and no album
_add(
    "2026-01-05T10:40:00Z",
    "Untitled Demo",
    [(None, "Unknown Band")],
    "Demo Tape",
    None,
    None,
    "unmapped",
)
_add(
    "2026-01-05T10:50:00Z",
    "Single Without Album",
    [("nocountry", "Nameless Duo")],
    None,
    "r_single",
    None,
    "no-album",
)
# same second, distinct msids
for i in range(3):
    _add(
        "2026-02-10T12:00:00Z",
        "Recording Level" if i < 2 else "Untitled Demo",
        [US] if i < 2 else [(None, "Unknown Band")],
        "Album A" if i < 2 else None,
        "r_rec" if i < 2 else None,
        "g_rec" if i < 2 else None,
        f"same-second-{i}",
    )
# XW / XE and mixed credits
_add(
    "2026-02-11T09:00:00Z",
    "Worldwide",
    [("xw", "Global Collective")],
    "Album W",
    "r_xw",
    "g_xw",
    "xw",
)
_add("2026-02-11T09:05:00Z", "Europe", [("xe", "Euro Ensemble")], "Album E", "r_xe", "g_xe", "xe")
_add(
    "2026-02-11T09:10:00Z",
    "Mixed Credits",
    [("xw", "Global Collective"), RO],
    "Album M",
    "r_mix",
    "g_mix",
    "xw-ro",
)
# DST: Europe/Chisinau (2026-03-29 01:00Z spring, 2026-10-25 01:00Z fall)
for t in (
    "2026-03-28T21:30:00Z",
    "2026-03-28T22:30:00Z",
    "2026-03-29T00:59:59Z",
    "2026-03-29T01:00:00Z",
    "2026-10-24T20:59:59Z",
    "2026-10-24T21:00:00Z",
    "2026-10-25T00:30:00Z",
    "2026-10-25T01:30:00Z",
):
    _add(t, "Chisinau DST", [RO], "Album B", "r_rg", "g_rg", "dst-chisinau")
# DST: America/New_York (2026-03-08 07:00Z spring, 2026-11-01 06:00Z fall, 01:30 repeats)
for t in (
    "2026-03-08T06:59:59Z",
    "2026-03-08T07:00:00Z",
    "2026-11-01T05:30:00Z",
    "2026-11-01T06:30:00Z",
):
    _add(t, "New York DST", [US], "Album A", "r_rec", "g_rec", "dst-newyork")
# non-whole-hour offsets: Asia/Kolkata +5:30, Asia/Kathmandu +5:45
for t in (
    "2026-01-15T18:29:59Z",
    "2026-01-15T18:30:00Z",
    "2026-01-15T18:14:59Z",
    "2026-01-15T18:15:00Z",
):
    _add(t, "Odd Offset", [JP], "Album C", "r_art", "g_art", "odd-offset")

# Pacific/Apia skipped 2011-12-30 completely; PostgreSQL owns boundary interpretation.
for t in ("2011-12-30T09:59:59Z", "2011-12-30T10:00:00Z"):
    _add(t, "Skipped Local Date", [JP], "Album C", "r_art", "g_art", "apia-skipped-date")

LISTENS = _L
