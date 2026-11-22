"""Public history orchestration: gate, canonical timezone and snapshot-bound cursors."""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from fastapi import HTTPException

from music_dashboard.config import settings
from music_dashboard.infrastructure.persistence.database import create_db_engine
from music_dashboard.infrastructure.persistence.public_history_queries import SQLPublicHistory
from music_dashboard.infrastructure.persistence.timezones import canonical_timezone
from music_dashboard.infrastructure.public_feed_cursors import cursor_store
from music_dashboard.presentation.api.schemas.history import HistoryPage


@dataclass(frozen=True)
class _HistoryCursor:
    timezone: str
    limit: int
    generation: str
    reference: datetime
    boundary: tuple[datetime, int]


@lru_cache(maxsize=1)
def history_repository() -> SQLPublicHistory:
    return SQLPublicHistory(create_db_engine(settings.database_url))


class PublicHistory:
    def page(self, tz: str = "UTC", limit: int = 50, cursor: str | None = None) -> HistoryPage:
        if not settings.public_event_feeds_enabled or settings.fixture_mode:
            raise HTTPException(503, "Public event feeds disabled")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HTTPException(400, "Invalid page limit")
        if not isinstance(tz, str) or len(tz) > 256:
            raise HTTPException(400, "Invalid timezone")
        timezone = canonical_timezone(tz) or "UTC"
        state = cursor_store.read(cursor) if cursor is not None else None
        if state is not None and (
            not isinstance(state, _HistoryCursor)
            or state.timezone != timezone or state.limit != limit
        ):
            raise HTTPException(409, "Feed cursor changed; reload feed")
        batch = history_repository().fetch(
            timezone, limit,
            reference=state.reference if state else None,
            generation=state.generation if state else None,
            boundary=state.boundary if state else None,
        )
        next_cursor = None
        if batch.boundary is not None:
            next_cursor = cursor_store.issue(_HistoryCursor(
                timezone, limit, batch.generation, batch.reference, batch.boundary,
            ))
        return HistoryPage(
            items=batch.items, next_cursor=next_cursor,
            generation=batch.generation, timezone=timezone,
        )
