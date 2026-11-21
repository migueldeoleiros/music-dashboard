from typing import Any

from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.domain.statistics.models import StatisticsFilter


class GetStatistics:
    def __init__(self, queries: StatisticsQueryPort) -> None:
        self.queries = queries

    def execute(self, family: str, filters: StatisticsFilter) -> dict[str, Any]:
        return self.queries.query(family, filters)
