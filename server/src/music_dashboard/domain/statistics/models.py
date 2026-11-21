from dataclasses import dataclass
from datetime import date
from typing import Literal

Dimension = Literal["artists", "albums", "tracks", "genres", "countries", "listens"]


@dataclass(frozen=True)
class StatisticsFilter:
    """Inclusive local dates. Only PostgreSQL computes UTC boundaries/buckets."""

    from_date: date
    to_date: date
    timezone: str
    limit: int
