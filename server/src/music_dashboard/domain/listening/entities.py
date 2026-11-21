from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

MAX_EPOCH_SECONDS = 253402300799  # 9999-12-31T23:59:59Z


def epoch_seconds(value: object) -> int | None:
    """Exact stored epoch seconds for a ``listened_at`` value, or None (never invent).

    Accepts ints and finite, integral, in-range floats (the real LB export writes e.g.
    ``1790856000.0``). Rejects bool, strings, NaN/inf, negative, non-integral and out-of-range
    values. Integral floats this small are exactly representable, so no precision is lost.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_EPOCH_SECONDS else None
    if isinstance(value, float):
        if not isfinite(value) or value != int(value):
            return None
        return int(value) if 0 <= value <= MAX_EPOCH_SECONDS else None
    return None


@dataclass(frozen=True)
class ListenIdentity:
    user: str
    listened_at: int
    recording_msid: str


@dataclass(frozen=True)
class RawListen:
    identity: ListenIdentity
    payload: dict[str, Any]
    provenance: str
    received_at: datetime


@dataclass(frozen=True)
class ExportSnapshot:
    """Two distinct notions of completeness.

    * deletion-safe (``complete``): only an account-bound snapshot with a trusted manifest
      (``expected_count``) may authorize deletion. A global live listen count never does.
    * import-complete: right account verified, archive iterated to the end without error and
      at least ``lower_bound`` rows (listen-count fetched before the export was created).
      Lets a run upsert and advance checkpoints, never tombstone.

    Iteration may still fail validation. Consumers MUST exhaust/stage rows before
    activating.
    """

    rows: Iterable[dict[str, Any]]
    user: str
    expected_count: int | None
    complete: bool
    lower_bound: int | None = None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)
