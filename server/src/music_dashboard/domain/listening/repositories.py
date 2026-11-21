from collections.abc import Iterable
from typing import Protocol

from music_dashboard.domain.listening.entities import RawListen


class ListeningRepository(Protocol):
    """MSID identity is stable across metadata remaps; never collapse timestamp ties."""

    def upsert(self, listens: Iterable[RawListen]) -> int: ...
    def quarantine(self, payload: dict[str, object], reason: str, provenance: str) -> None: ...


class SyncStateRepository(Protocol):
    """Implementations own transactions, advisory locking and durable checkpoints."""

    def try_lock(self, user: str) -> bool: ...
    def load_checkpoint(self, user: str) -> dict[str, object]: ...
    def save_checkpoint(self, user: str, checkpoint: dict[str, object]) -> None: ...
