from collections.abc import Iterable
from typing import Any, Protocol

from music_dashboard.domain.metadata.entities import EntityType
from music_dashboard.domain.statistics.models import StatisticsFilter


class StatisticsQueryPort(Protocol):
    """SQL aggregates only; never hydrate complete listen history."""

    def query(self, family: str, filters: StatisticsFilter) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...


class ListenBrainzClientPort(Protocol):
    """One exclusive bound per request; full same-second ambiguity requires export."""

    def page(
        self,
        user: str,
        *,
        max_ts: int | None = None,
        min_ts: int | None = None,
        count: int = 1000,
    ) -> list[dict[str, Any]]: ...
    def export(self, user: str) -> Iterable[dict[str, Any]]: ...
    def recording_metadata(self, mbids: list[str]) -> list[dict[str, Any]]: ...


class MusicBrainzClientPort(Protocol):
    def genres(self, entity_type: EntityType, mbid: str) -> dict[str, Any]: ...
