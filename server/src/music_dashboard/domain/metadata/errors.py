"""Errors raised by metadata source adapters. Messages never contain URLs or payloads."""


class MetadataSourceError(Exception):
    """Base class for metadata adapter failures."""


class MetadataTransientError(MetadataSourceError):
    """Retryable failure (rate limit, 5xx, network). Caller schedules durable retry."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class MetadataPermanentError(MetadataSourceError):
    """Entity-specific bad request, or source-wide authentication/configuration failure."""

    def __init__(self, reason: str, *, source_wide: bool = False) -> None:
        super().__init__(reason)
        self.source_wide = source_wide


TransientMetadataError = MetadataTransientError  # alias
