import re
from datetime import date
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import HTTPException, Query
from pydantic import BeforeValidator, Field

from music_dashboard.application.get_statistics import GetStatistics
from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.config import settings
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.fixture_statistics import FixtureStatisticsQueries
from music_dashboard.infrastructure.persistence.database import create_db_engine
from music_dashboard.infrastructure.persistence.statistics_queries import SQLStatisticsQueries
from music_dashboard.infrastructure.persistence.timezones import load_timezones


@lru_cache(maxsize=1)
def statistics_queries() -> StatisticsQueryPort:
    if settings.fixture_mode:
        return FixtureStatisticsQueries()
    return SQLStatisticsQueries(create_db_engine(settings.database_url))


def get_statistics() -> GetStatistics:
    return GetStatistics(statistics_queries())


def exact_local_date(value: object) -> object:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("Expected a local calendar date in YYYY-MM-DD format")
    return value


LocalDate = Annotated[
    date,
    BeforeValidator(exact_local_date),
    Field(json_schema_extra={"pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}),
]


def summary_filter(
    from_date: Annotated[LocalDate, Query(alias="from", description="Inclusive local start date")],
    to_date: Annotated[LocalDate, Query(alias="to", description="Inclusive local end date")],
    tz: Annotated[str, Query(description="Supported IANA timezone, canonicalized")],
) -> StatisticsFilter:
    return statistics_filter(from_date, to_date, tz)


def statistics_filter(
    from_date: Annotated[LocalDate, Query(alias="from", description="Inclusive local start date")],
    to_date: Annotated[LocalDate, Query(alias="to", description="Inclusive local end date")],
    tz: Annotated[str, Query(description="Supported IANA timezone, canonicalized")],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    metric: Annotated[
        Literal["listens", "minutes"],
        Query(description="Rankings, heatmap and countries only: count listens or minutes"),
    ] = "listens",
) -> StatisticsFilter:
    # Validation only: never compute date boundaries or buckets in Python.
    zones = load_timezones()
    if from_date > to_date:
        raise HTTPException(400, "from must be on or before to")
    if tz not in zones:
        raise HTTPException(400, "Unsupported IANA timezone")
    return StatisticsFilter(from_date, to_date, zones[tz], limit, metric=metric)
