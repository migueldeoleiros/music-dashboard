from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.domain.statistics.policies import StatisticsUnavailable
from music_dashboard.presentation.api.dependencies import statistics_queries
from music_dashboard.presentation.api.schemas.statistics import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse, operation_id="health")
def health(
    queries: Annotated[StatisticsQueryPort, Depends(statistics_queries)],
) -> Any:
    try:
        return queries.health()
    except StatisticsUnavailable:
        return JSONResponse(
            status_code=503,
            content={
                "code": "unavailable",
                "message": "Statistics temporarily unavailable",
                "details": [],
            },
        )
