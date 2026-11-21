"""Frozen v1 public aggregate contract. No per-listen timestamps or raw payloads."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Dimension = Literal["artists", "albums", "tracks", "genres", "countries", "listens"]
NonNegative = Annotated[float, Field(ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coverage(ContractModel):
    total_listens: int = Field(ge=0)
    included_listens: int = Field(ge=0)
    excluded_listens: int = Field(ge=0)

    @model_validator(mode="after")
    def balanced(self) -> "Coverage":
        if self.total_listens != self.included_listens + self.excluded_listens:
            raise ValueError("included + excluded must equal total")
        return self


class DimensionCoverage(ContractModel):
    artists: Coverage
    albums: Coverage
    tracks: Coverage
    genres: Coverage
    countries: Coverage
    listens: Coverage


class DateRange(ContractModel):
    from_date: date = Field(serialization_alias="from")
    to_date: date = Field(serialization_alias="to")
    tz: str = Field(description="Canonical supported IANA timezone")


class AggregateResponse(ContractModel):
    range: DateRange
    coverage: DimensionCoverage
    last_updated: datetime | None = Field(description="UTC ingestion freshness; null before import")
    generation: int = Field(
        ge=0, description="Combined ingestion/enrichment invalidation generation"
    )
    fixture: bool = Field(description="True only for clearly marked synthetic stub responses")


class RankedEntity(ContractModel):
    key: str = Field(description="Stable MBID or normalized namespaced fallback identity")
    label: str
    mbid: str | None
    metadata_state: Literal["resolved", "unmapped", "missing"]
    listens: NonNegative
    share: float = Field(ge=0, le=1, description="Fraction of dimension included listens")


class RankingResponse(AggregateResponse):
    dimension: Literal["artists", "albums", "tracks", "genres"]
    items: list[RankedEntity]


class HeatmapCell(ContractModel):
    day: date
    listens: int = Field(ge=0)


class HeatmapResponse(AggregateResponse):
    week_starts_on: Literal["monday"] = "monday"
    cells: list[HeatmapCell]


class CountryValue(ContractModel):
    code: str = Field(
        pattern="^(?:[A-WY-Z][A-Z]|X[A-DF-VX-Z])$", description="Artist-associated ISO country"
    )
    listens: NonNegative
    share: float = Field(ge=0, le=1)


class CountryResponse(AggregateResponse):
    countries: list[CountryValue]
    partial_credit_note: str = "Only country-resolved unique credited artists share each listen."


class Bucket(ContractModel):
    start: date
    end: date = Field(description="Exclusive local date")
    partial: bool


class EvolutionSeries(ContractModel):
    key: str
    label: str
    is_other: bool = Field(description="Known categories outside whole-range top N, never unknown")
    values: list[NonNegative] = Field(
        description="Zero-filled, one value per bucket; not cumulative"
    )


class EvolutionResponse(AggregateResponse):
    dimension: Literal["artists", "genres"]
    bucket_unit: Literal["day", "week", "month"]
    buckets: list[Bucket]
    series: list[EvolutionSeries]


class PieSlice(ContractModel):
    key: str
    label: str
    is_other: bool
    listens: NonNegative
    share: float = Field(ge=0, le=1)


class PieResponse(AggregateResponse):
    dimension: Literal["artists", "genres"]
    slices: list[PieSlice]


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded"]
    mode: Literal["fixture", "live"]
    last_updated: datetime | None
    sync_lag_seconds: int | None = Field(ge=0, description="Null if no successful import")
    coverage: DimensionCoverage
    generation: int = Field(ge=0)


class ErrorResponse(ContractModel):
    code: Literal["invalid_request", "unavailable"]
    message: str
    details: list[str]
