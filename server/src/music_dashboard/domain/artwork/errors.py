"""Adapter failures. Messages never contain URLs, payloads or credentials."""


class ArtworkTransientError(Exception):
    """Retryable (rate limit, 5xx, network, malformed upstream body)."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class ArtworkQuotaError(ArtworkTransientError):
    """Deezer error code 4: source-wide quota. The caller stops using the source for a while."""
