from typing import Annotated

from fastapi import APIRouter, Depends

from music_dashboard.application.ports import StatisticsQueryPort
from music_dashboard.bootstrap import statistics_queries
from music_dashboard.presentation.api.schemas.statistics import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse, operation_id="health")
def health(
    queries: Annotated[StatisticsQueryPort, Depends(statistics_queries)],
) -> dict[str, object]:
    return queries.health()
