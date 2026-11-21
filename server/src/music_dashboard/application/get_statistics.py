from typing import Any

from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    COUNTRY_ARTISTS_FAMILY,
    SUPPORTED_FAMILIES,
    InvalidStatisticsRequest,
    validate_country_code,
    validate_filters,
)


class GetStatistics:
    def __init__(self, queries: StatisticsQueryPort) -> None:
        self.queries = queries

    def execute(self, family: str, filters: StatisticsFilter) -> dict[str, Any]:
        validate_filters(filters.from_date, filters.to_date, filters.limit)
        if family not in SUPPORTED_FAMILIES:
            raise InvalidStatisticsRequest("Unsupported chart family")
        if family == COUNTRY_ARTISTS_FAMILY:
            if filters.country_code is None:
                raise InvalidStatisticsRequest("Unsupported country code")
            validate_country_code(filters.country_code)
        elif filters.country_code is not None:
            raise InvalidStatisticsRequest("Country code is not supported for this family")
        return self.queries.query(family, filters)
