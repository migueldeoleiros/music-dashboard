from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
    """Only an account-bound completed snapshot manifest can authorize deletion.

    Iteration may still fail validation. Consumers MUST exhaust/stage rows before
    activating. A global live listen count is not snapshot completeness evidence.
    """

    rows: Iterable[dict[str, Any]]
    user: str
    expected_count: int | None
    complete: bool

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)
