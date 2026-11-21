from typing import Annotated, Any

from fastapi import APIRouter, Depends

from music_dashboard.application.get_statistics import GetStatistics
from music_dashboard.bootstrap import get_statistics
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.presentation.api.dependencies import statistics_filter
from music_dashboard.presentation.api.schemas.statistics import (
    CountryResponse,
    ErrorResponse,
    EvolutionResponse,
    HeatmapResponse,
    PieResponse,
    RankingResponse,
)

router = APIRouter(prefix="/api", tags=["statistics"], responses={400: {"model": ErrorResponse}})


def endpoint(family: str) -> Any:
    def get(
        filters: Annotated[StatisticsFilter, Depends(statistics_filter)],
        use_case: Annotated[GetStatistics, Depends(get_statistics)],
    ) -> dict[str, Any]:
        return use_case.execute(family, filters)

    return get


for family, response in (
    ("rankings/artists", RankingResponse),
    ("rankings/albums", RankingResponse),
    ("rankings/tracks", RankingResponse),
    ("rankings/genres", RankingResponse),
    ("heatmap", HeatmapResponse),
    ("countries", CountryResponse),
    ("evolution/artists", EvolutionResponse),
    ("evolution/genres", EvolutionResponse),
    ("pies/artists", PieResponse),
    ("pies/genres", PieResponse),
):
    router.add_api_route(
        f"/{family}",
        endpoint(family),
        methods=["GET"],
        response_model=response,
        operation_id=family.replace("/", "_"),
    )
