"""Additive public routes: DTO allowlists are independent of frozen aggregates."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from music_dashboard.presentation.api.schemas.history import (
    ArtistCredits,
    ArtistHeatmap,
    ArtistRankingPage,
    ArtistRecentPage,
    ArtistSummary,
    HistoryPage,
)
from music_dashboard.presentation.api.schemas.statistics import ErrorResponse

router = APIRouter(
    prefix="/api", tags=["artist-history"],
    responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 503)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=256)]


def artist_service() -> Any:
    from music_dashboard.application.artist_history import ArtistHistory

    return ArtistHistory()


@router.get("/artists/{artist_key}", response_model=ArtistSummary)
def artist_summary(artist_key: str) -> Any:
    return artist_service().summary(artist_key)


@router.get("/artists/{artist_key}/rankings/{dimension}", response_model=ArtistRankingPage)
def artist_rankings(
    artist_key: str, dimension: Literal["albums", "tracks"],
    limit: Limit = 50, cursor: Cursor = None,
) -> Any:
    return artist_service().rankings(artist_key, dimension, limit=limit, cursor=cursor)


@router.get("/artists/{artist_key}/heatmap", response_model=ArtistHeatmap)
def artist_heatmap(
    artist_key: str, year: Annotated[int | None, Query(ge=1, le=9999)] = None,
    tz: Annotated[str, Query(max_length=100)] = "UTC",
) -> Any:
    return artist_service().heatmap(artist_key, year=year, tz=tz)


@router.get("/artists/{artist_key}/recent", response_model=ArtistRecentPage)
def artist_recent(artist_key: str, limit: Limit = 50, cursor: Cursor = None) -> Any:
    return artist_service().recent(artist_key, limit=limit, cursor=cursor)


@router.get("/artist-credits", response_model=ArtistCredits)
def artist_credits(dimension: Literal["albums", "tracks"], key: str) -> Any:
    return artist_service().credits(dimension, key)


@router.get("/history", response_model=HistoryPage)
def public_history(
    tz: Annotated[str, Query(max_length=100)] = "UTC", limit: Limit = 50,
    cursor: Cursor = None,
) -> Any:
    from music_dashboard.application.public_history import PublicHistory

    return PublicHistory().page(tz=tz, limit=limit, cursor=cursor)
