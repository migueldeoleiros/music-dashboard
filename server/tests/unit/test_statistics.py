from dataclasses import replace
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from music_dashboard.app import app
from music_dashboard.application.get_statistics import GetStatistics
from music_dashboard.domain.statistics.models import Coverage, StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    InvalidStatisticsRequest,
    StatisticsUnavailable,
    normalized_name,
    validate_filters,
)
from music_dashboard.infrastructure.persistence.statistics_queries import (
    ResponseCache,
    SQLStatisticsQueries,
)
from music_dashboard.presentation.api.dependencies import get_statistics, statistics_queries


def test_domain_identity_and_coverage():
    assert normalized_name("\u00a0Ｓtraße\u2003") == "strasse"
    assert normalized_name("Band feat. Duo & Guest") == "band feat. duo & guest"
    assert Coverage(4, 3).as_dict() == {
        "total_listens": 4,
        "included_listens": 3,
        "excluded_listens": 1,
    }
    for total, included in ((-1, 0), (1, -1), (1, 2)):
        with pytest.raises(ValueError):
            Coverage(total, included)


def test_cache_ttl_lru_bytes_and_copy_isolation():
    with patch("music_dashboard.infrastructure.persistence.statistics_queries.monotonic") as clock:
        clock.return_value = 10
        cache = ResponseCache(ttl=5, max_entries=2, max_bytes=200)
        value = {"items": [1]}
        cache.put((1,), value)
        value["items"].append(2)
        assert cache.get((1,)) == {"items": [1]}
        copy = cache.get((1,))
        copy["items"].append(3)
        assert cache.get((1,)) == {"items": [1]}
        cache.put((2,), value)
        cache.get((1,))  # 2 becomes least recently used
        cache.put((3,), value)
        assert cache.get((2,)) is None
        assert len(cache.entries) == 2
        clock.return_value = 15
        assert cache.get((1,)) is None
        cache.put((4,), {"huge": "x" * 300})
        assert cache.get((4,)) is None
        cache.put((5,), {"sized": "x" * 100})
        cache.put((6,), {"sized": "x" * 100})
        assert cache.bytes <= 200
        assert cache.get((5,)) is None


@pytest.mark.parametrize("tz,limit", [("+05:30", 10), ("UTC+5", 10), ("UTC", 0), ("UTC", 101)])
def test_adapter_validates_before_database_access(tz, limit):
    q = SQLStatisticsQueries(create_engine("sqlite://"))
    with pytest.raises(InvalidStatisticsRequest):
        q.query("heatmap", StatisticsFilter(date(2026, 1, 1), date(2026, 1, 1), tz, limit))


def test_validate_filters_accepts_366_day_leap_year_window():
    # 2024 is a leap year: Jan 1..Dec 31 is 366 inclusive local days.
    assert (date(2024, 12, 31) - date(2024, 1, 1)).days + 1 == 366
    validate_filters(date(2024, 1, 1), date(2024, 12, 31), 10)
    # Rolling-365 heatmap spanning a leap day, and one day more than a non-leap year.
    validate_filters(date(2023, 3, 1), date(2024, 2, 29), 10)
    validate_filters(date(2025, 1, 1), date(2025, 12, 31), 10)
    with pytest.raises(InvalidStatisticsRequest):
        validate_filters(date(2024, 12, 31), date(2024, 1, 1), 10)


@pytest.mark.parametrize("code", ["XX", "ZZ", "XW", "XE", "us", ""])
def test_country_artists_invalid_code_rejected_before_database_access(code):
    q = SQLStatisticsQueries(create_engine("sqlite://"))
    with pytest.raises(InvalidStatisticsRequest):
        q.query(
            "countries/artists",
            StatisticsFilter(date(2026, 1, 1), date(2026, 1, 1), "UTC", 10, code),
        )


def test_use_case_requires_code_only_for_country_artists():
    class Never:
        def query(self, family, filters):
            raise AssertionError("must not reach the database")

    use_case = GetStatistics(Never())
    base = StatisticsFilter(date(2026, 1, 1), date(2026, 1, 1), "UTC", 10)
    with pytest.raises(InvalidStatisticsRequest):
        use_case.execute("countries/artists", base)
    with pytest.raises(InvalidStatisticsRequest):
        use_case.execute("countries", replace(base, country_code="US"))


def test_public_unavailability_never_leaks_driver_details():
    class BrokenQueries:
        def query(self, family, filters):
            raise StatisticsUnavailable("private driver diagnostics")

        def health(self):
            raise StatisticsUnavailable("private driver diagnostics")

    app.dependency_overrides[get_statistics] = lambda: GetStatistics(BrokenQueries())
    app.dependency_overrides[statistics_queries] = BrokenQueries
    try:
        client = TestClient(app)
        for path in ("/api/heatmap", "/api/health"):
            response = client.get(
                path, params={"from": "2026-01-01", "to": "2026-01-01", "tz": "UTC"}
            )
            assert response.status_code == 503
            assert response.json() == {
                "code": "unavailable",
                "message": "Statistics temporarily unavailable",
                "details": [],
            }
            assert "private" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_evolution_has_no_special_default_limit_without_contract_drift():
    class RecordingQueries:
        def query(self, family, filters):
            assert filters.limit == expected_limit[0]
            # Deliberate application error lets the test inspect parameters without
            # inventing a chart response or bypassing production validation.
            raise InvalidStatisticsRequest("captured")

    expected_limit = [10]
    app.dependency_overrides[get_statistics] = lambda: GetStatistics(RecordingQueries())
    try:
        client = TestClient(app)
        for expected, extra in ((10, {}), (10, {"limit": 10}), (2, {"limit": 2})):
            expected_limit[0] = expected
            response = client.get(
                "/api/evolution/artists",
                params={"from": "2026-01-01", "to": "2026-01-01", "tz": "UTC", **extra},
            )
            assert response.status_code == 400
            assert response.json()["message"] == "captured"
    finally:
        app.dependency_overrides.clear()
