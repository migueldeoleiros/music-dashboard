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
    # Only for the path-param family countries/artists (validated against COUNTRY_CODES).
    country_code: str | None = None
    # "minutes" weighs listens by track length. Honoured by MINUTES_FAMILIES only.
    metric: str = "listens"


@dataclass(frozen=True)
class Coverage:
    total_listens: int
    included_listens: int

    def __post_init__(self) -> None:
        if not 0 <= self.included_listens <= self.total_listens:
            raise ValueError("Coverage must satisfy 0 <= included <= total")

    def as_dict(self) -> dict[str, int]:
        return {
            "total_listens": self.total_listens,
            "included_listens": self.included_listens,
            "excluded_listens": self.total_listens - self.included_listens,
        }
