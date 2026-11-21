"""Artwork scheduling policy: TTLs with jitter and capped exponential transient backoff."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ArtworkPolicy:
    positive_ttl: timedelta = timedelta(days=30)
    miss_ttl: timedelta = timedelta(days=7)
    retry_base_seconds: float = 60
    retry_max_seconds: float = 86400
    jitter: float = 0.2  # +/- fraction applied to positive and miss TTLs

    def __post_init__(self) -> None:
        if (
            min(
                self.positive_ttl.total_seconds(),
                self.miss_ttl.total_seconds(),
                self.retry_base_seconds,
                self.retry_max_seconds,
            )
            <= 0
            or not 0 <= self.jitter < 1
        ):
            raise ValueError("Artwork TTLs and retry delays must be positive; jitter in [0,1)")

    def ttl(self, resolved: bool, rand: Callable[[], float]) -> timedelta:
        """rand() is uniform in [0,1); result is ttl * (1 +/- jitter)."""
        base = self.positive_ttl if resolved else self.miss_ttl
        return base * (1 + self.jitter * (2 * rand() - 1))

    def backoff(self, attempts: int, retry_after: float | None = None) -> timedelta:
        """attempts counts failures including this one (>=1)."""
        seconds = min(self.retry_max_seconds, self.retry_base_seconds * 2 ** min(attempts - 1, 20))
        return timedelta(seconds=max(seconds, min(retry_after or 0, self.retry_max_seconds)))
