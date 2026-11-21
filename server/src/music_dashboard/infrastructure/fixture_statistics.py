"""Contract-only synthetic responses, NOT statistics implementation (T-005 owns SQL)."""

import json
from pathlib import Path
from typing import Any, cast

from music_dashboard.domain.statistics.models import StatisticsFilter

DIMENSIONS = ("artists", "albums", "tracks", "genres", "countries", "listens")


def empty_coverage() -> dict[str, dict[str, int]]:
    return {
        dimension: {"total_listens": 0, "included_listens": 0, "excluded_listens": 0}
        for dimension in DIMENSIONS
    }


class FixtureStatisticsQueries:
    def query(self, family: str, filters: StatisticsFilter) -> dict[str, Any]:
        if (
            filters.from_date.isoformat() == "2026-01-05"
            and filters.to_date.isoformat() == "2026-01-05"
            and filters.timezone == "Etc/UTC"
            and (filters.limit == 1 or (family.startswith("rankings/") and filters.limit == 2))
            and (family != "countries/artists" or filters.country_code == "US")
        ):
            examples = json.loads(Path(__file__).with_name("aggregate_responses.json").read_text())
            example = cast(dict[str, Any], examples[family])
            if family.startswith("rankings/") or family == "countries/artists":
                example["items"] = example["items"][: filters.limit]
            return example
        result: dict[str, Any] = {
            "range": {
                "from_date": filters.from_date,
                "to_date": filters.to_date,
                "tz": filters.timezone,
            },
            "coverage": empty_coverage(),
            "last_updated": None,
            "generation": 0,
            "fixture": True,
        }
        kind, _, dimension = family.partition("/")
        if family == "countries/artists":
            result.update(dimension="artists", items=[])
        elif kind == "rankings":
            result.update(dimension=dimension, items=[])
        elif kind == "heatmap":
            result.update(week_starts_on="monday", cells=[])
        elif kind == "countries":
            result.update(countries=[])
        elif kind == "evolution":
            result.update(dimension=dimension, bucket_unit="day", buckets=[], series=[])
        elif kind == "pies":
            result.update(dimension=dimension, slices=[])
        return result

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "fixture",
            "last_updated": None,
            "sync_lag_seconds": None,
            "coverage": empty_coverage(),
            "generation": 0,
        }
