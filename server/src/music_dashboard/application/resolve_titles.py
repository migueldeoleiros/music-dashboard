"""Bounded release / release-group title resolution (worker only, exact MBID, no cover data).

Per run: deduplicated candidates, at most `max_requests` upstream calls and a wall-clock
deadline. Transient failure backs off exponentially (honouring Retry-After) and ends the run;
404 and rejected (mismatched) responses are negative-cached. Resolved rows refresh on a long
TTL. No DB transaction is open during network calls.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from music_dashboard.domain.metadata.errors import (
    MetadataPermanentError,
    MetadataTransientError,
    TitleDeadlineError,
)

MAX_BACKOFF_SECONDS = 86400.0  # absolute ceiling for any server-supplied delay


class TitleClientPort(Protocol):
    def title(
        self, entity_type: str, mbid: str, timeout: float | None = None
    ) -> dict[str, Any] | None: ...


class TitleCachePort(Protocol):
    def source_retry_at(self) -> datetime | None: ...
    def candidates(self, now: datetime, limit: int) -> list[tuple[str, str, int]]: ...
    def save_resolved(
        self,
        entity_type: str,
        mbid: str,
        title: str,
        release_group_mbid: str | None,
        next_retry_at: datetime,
        now: datetime,
    ) -> bool: ...
    def save_miss(
        self,
        entity_type: str,
        mbid: str,
        status: str,
        next_retry_at: datetime,
        now: datetime,
        attempts: int,
    ) -> None: ...


@dataclass(frozen=True)
class TitlePolicy:
    max_requests: int = 25
    deadline_seconds: float = 120.0
    resolved_ttl: timedelta = timedelta(days=30)
    not_found_ttl: timedelta = timedelta(days=7)
    retry_base_seconds: float = 30
    retry_max_seconds: float = 86400

    def __post_init__(self) -> None:
        if (
            self.max_requests < 1
            or not all(
                math.isfinite(v)
                for v in (
                    self.deadline_seconds,
                    self.resolved_ttl.total_seconds(),
                    self.not_found_ttl.total_seconds(),
                    self.retry_base_seconds,
                    self.retry_max_seconds,
                )
            )
            or min(
                self.deadline_seconds,
                self.resolved_ttl.total_seconds(),
                self.not_found_ttl.total_seconds(),
                self.retry_base_seconds,
                self.retry_max_seconds,
            )
            <= 0
        ):
            raise ValueError("Title policy limits must be positive")


class ResolveTitles:
    def __init__(
        self,
        repository: TitleCachePort,
        client: TitleClientPort,
        policy: TitlePolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.client = client
        self.policy = policy or TitlePolicy()
        self.clock = clock
        self.monotonic = monotonic

    def run(self) -> dict[str, int]:
        stats = {"requests": 0, "resolved": 0, "published": 0, "not_found": 0, "transient": 0}
        started = self.monotonic()
        cooldown = self.repository.source_retry_at()
        if cooldown is not None and cooldown > self.clock():
            return stats  # source-wide backoff from an earlier run still active
        seen: set[tuple[str, str]] = set()
        # Over-fetch so rows deduped/skipped cannot starve the budget.
        for entity_type, mbid, attempts in self.repository.candidates(
            self.clock(), self.policy.max_requests
        ):
            if (entity_type, mbid) in seen:
                continue
            seen.add((entity_type, mbid))
            remaining = self.policy.deadline_seconds - (self.monotonic() - started)
            if stats["requests"] >= self.policy.max_requests or remaining <= 0:
                break
            stats["requests"] += 1
            now = self.clock()
            try:
                found = self.client.title(entity_type, mbid, timeout=remaining)
            except TitleDeadlineError:
                break  # nothing requested or written; candidate stays due
            except MetadataTransientError as error:
                self._transient(entity_type, mbid, attempts, error.retry_after, stats)
                break  # source is unhealthy / rate limited: stop this run
            except MetadataPermanentError as error:
                if error.source_wide:
                    self._transient(entity_type, mbid, attempts, None, stats)
                    break
                found = None
            if found is None:
                self.repository.save_miss(
                    entity_type, mbid, "not_found", now + self.policy.not_found_ttl, now, 0
                )
                stats["not_found"] += 1
                continue
            published = self.repository.save_resolved(
                entity_type,
                mbid,
                found["title"],
                found.get("release_group_mbid"),
                now + self.policy.resolved_ttl,
                now,
            )
            stats["resolved"] += 1
            stats["published"] += int(published)
        return stats

    def _transient(
        self,
        entity_type: str,
        mbid: str,
        attempts: int,
        retry_after: float | None,
        stats: dict[str, int],
    ) -> None:
        attempts += 1
        seconds = min(
            self.policy.retry_max_seconds,
            self.policy.retry_base_seconds * 2 ** min(attempts - 1, 20),
        )
        if retry_after is not None and math.isfinite(retry_after):
            seconds = max(seconds, retry_after)  # same rule as genre enrichment
        seconds = min(seconds, self.policy.retry_max_seconds, MAX_BACKOFF_SECONDS)
        now = self.clock()
        self.repository.save_miss(
            entity_type, mbid, "transient", now + timedelta(seconds=seconds), now, attempts
        )
        stats["transient"] += 1
