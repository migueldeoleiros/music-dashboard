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
