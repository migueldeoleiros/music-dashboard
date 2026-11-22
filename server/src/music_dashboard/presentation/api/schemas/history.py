"""Additive public artist/history DTOs. Never inherit aggregate freshness fields."""

from typing import Literal

from pydantic import Field

from music_dashboard.presentation.api.schemas.statistics import ContractModel, HeatmapCell


class ArtistCredit(ContractModel):
    key: str | None
    label: str


class ArtistCredits(ContractModel):
    artists: list[ArtistCredit]


class PublicSong(ContractModel):
    title: str
    artists: list[ArtistCredit]
    album: str | None = None
    image_url: str | None = None


class HistoryItem(PublicSong):
    # Calendar label only: no date/time or internal listen identity.
    group: str


class HistoryPage(ContractModel):
    items: list[HistoryItem]
    next_cursor: str | None
    generation: str
    timezone: str


class ArtistSummary(ContractModel):
    key: str
    label: str
    image_url: str | None = None
    genres: list[str]
    byline: str
    total_listens: int = Field(ge=0)


class ArtistRankedItem(ContractModel):
    key: str
    label: str
    listens: int = Field(ge=0)
    image_url: str | None = None


class ArtistRankingPage(ContractModel):
    dimension: Literal["albums", "tracks"]
    items: list[ArtistRankedItem]
    next_cursor: str | None
    generation: str


class ArtistHeatmap(ContractModel):
    year: int
    years: list[int]
    timezone: str
    week_starts_on: Literal["monday"] = "monday"
    cells: list[HeatmapCell]


class ArtistRecentPage(ContractModel):
    items: list[PublicSong]
    next_cursor: str | None
    generation: str
