from music_dashboard.application.get_statistics import GetStatistics
from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.config import settings
from music_dashboard.infrastructure.fixture_statistics import FixtureStatisticsQueries


def statistics_queries() -> StatisticsQueryPort:
    if not settings.fixture_mode:
        raise RuntimeError(
            "Live statistics adapter is not implemented; use MUSIC_FIXTURE_MODE=true"
        )
    return FixtureStatisticsQueries()


def get_statistics() -> GetStatistics:
    return GetStatistics(statistics_queries())
