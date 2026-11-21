from datetime import date
from typing import Annotated

from fastapi import HTTPException, Query

from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.infrastructure.persistence.timezones import load_timezones


def statistics_filter(
    from_date: Annotated[date, Query(alias="from", description="Inclusive local start date")],
    to_date: Annotated[date, Query(alias="to", description="Inclusive local end date")],
    tz: Annotated[str, Query(description="Supported IANA timezone, canonicalized")],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> StatisticsFilter:
    # Validation only: never compute date boundaries or buckets in Python.
    zones = load_timezones()
    if from_date > to_date:
        raise HTTPException(400, "from must be on or before to")
    if tz not in zones:
        raise HTTPException(400, "Unsupported IANA timezone")
    return StatisticsFilter(from_date, to_date, zones[tz], limit)
