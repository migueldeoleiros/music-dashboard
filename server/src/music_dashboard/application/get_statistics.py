from typing import Any

from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    FAMILIES,
    InvalidStatisticsRequest,
    validate_filters,
)


class GetStatistics:
    def __init__(self, queries: StatisticsQueryPort) -> None:
        self.queries = queries

    def execute(self, family: str, filters: StatisticsFilter) -> dict[str, Any]:
        validate_filters(filters.from_date, filters.to_date, filters.limit)
        if family not in FAMILIES:
            raise InvalidStatisticsRequest("Unsupported chart family")
        return self.queries.query(family, filters)
