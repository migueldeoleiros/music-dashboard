"""Controlled timezone metadata (tzdata 2025b == PyPI tzdata 2025.2).

`timezones.json`: {"tzdata_version": str, "zones": {name: canonical_name}}.
Every valid name is a key (canonical names map to themselves). Used for API
validation/canonicalization and to seed the `timezones` table. No boundary math here:
PostgreSQL does all bucketing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("timezones.json")


@lru_cache(maxsize=1)
def load_timezones() -> dict[str, str]:
    """alias-or-canonical name -> canonical name."""
    return dict(json.loads(_PATH.read_text(encoding="utf-8"))["zones"])


def canonical_timezone(name: str) -> str | None:
    """Canonical IANA name, or None if unsupported (case-sensitive, exact)."""
    return load_timezones().get(name)


def regenerate(tzdata_zi: Path, version: str) -> None:
    """Rebuild timezones.json from a tzdata.zi file (maintenance helper)."""
    zones: dict[str, str] = {}
    links: dict[str, str] = {}
    for line in tzdata_zi.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Z":
            zones[parts[1]] = parts[1]
        elif len(parts) == 3 and parts[0] == "L":
            links[parts[2]] = parts[1]
    for alias, target in links.items():
        while target in links:
            target = links[target]
        zones[alias] = target
    out = {"tzdata_version": version, "zones": dict(sorted(zones.items()))}
    _PATH.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    load_timezones.cache_clear()
