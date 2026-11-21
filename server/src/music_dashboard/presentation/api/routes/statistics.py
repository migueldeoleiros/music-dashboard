from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from music_dashboard.application.get_statistics import GetStatistics
from music_dashboard.domain.statistics.models import StatisticsFilter
from music_dashboard.domain.statistics.policies import (
    COUNTRY_ARTISTS_FAMILY,
    InvalidStatisticsRequest,
    StatisticsUnavailable,
)
from music_dashboard.presentation.api.dependencies import get_statistics, statistics_filter
from music_dashboard.presentation.api.schemas.statistics import (
    CountryResponse,
    ErrorResponse,
    EvolutionResponse,
    HeatmapResponse,
    PieResponse,
    RankingResponse,
)

router = APIRouter(prefix="/api", tags=["statistics"], responses={400: {"model": ErrorResponse}})


def run(use_case: GetStatistics, family: str, filters: StatisticsFilter) -> Any:
    try:
        return use_case.execute(family, filters)
    except InvalidStatisticsRequest as exc:
        raise HTTPException(400, str(exc)) from None
    except StatisticsUnavailable:
        return JSONResponse(
            status_code=503,
            content={
                "code": "unavailable",
                "message": "Statistics temporarily unavailable",
                "details": [],
            },
        )


def endpoint(family: str) -> Any:
    def get(
        filters: Annotated[StatisticsFilter, Depends(statistics_filter)],
        use_case: Annotated[GetStatistics, Depends(get_statistics)],
    ) -> Any:
        # `limit` only caps rankings; pies and evolution select series by share (> 2%) instead.
        return run(use_case, family, filters)

    return get


def country_artists(
    code: Annotated[str, Path(description="Uppercase ISO 3166-1 alpha-2 country code")],
    filters: Annotated[StatisticsFilter, Depends(statistics_filter)],
    use_case: Annotated[GetStatistics, Depends(get_statistics)],
) -> Any:
    # Invalid code -> 400 via the use case (never 404/422), before any database access.
    return run(use_case, COUNTRY_ARTISTS_FAMILY, replace(filters, country_code=code))


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

router.add_api_route(
    "/countries/{code}/artists",
    country_artists,
    methods=["GET"],
    response_model=RankingResponse,
    operation_id="countries_artists",
)
