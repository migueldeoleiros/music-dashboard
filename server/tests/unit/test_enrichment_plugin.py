"""Plugin safety/discovery and stdlib HTTP edge cases; never opens a socket."""

import argparse
import urllib.error
from datetime import UTC, datetime
from http.client import IncompleteRead
from io import BytesIO

import pytest

from music_dashboard.application.jobs.enrichment import run_once, tick
from music_dashboard.domain.metadata.errors import MetadataTransientError
from music_dashboard.infrastructure.musicbrainz.client import MusicBrainzConfig
from music_dashboard.infrastructure.musicbrainz.transport import (
    HttpRequest,
    parse_retry_after,
    rate_limit_exhausted_delay,
    urllib_transport,
)
from music_dashboard.presentation.cli_commands.enrich import configure, run


def test_fixture_worker_and_cli_never_call_upstream(monkeypatch):
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    tick()
    with pytest.raises(ValueError, match="fixture mode never calls HTTP"):
        run_once()
    with pytest.raises(SystemExit, match="--allow-network"):
        run(argparse.Namespace(allow_network=False, limit=100))


def test_nonfixture_requires_descriptive_contact_before_database(monkeypatch):
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.delenv("MUSIC_METADATA_USER_AGENT", raising=False)
    with pytest.raises(ValueError, match="user_agent"):
        run_once()
    for bad in ("python-urllib", "music-dashboard/0.1 (", "music-dashboard/0.1 (nobody)"):
        with pytest.raises(ValueError):
            MusicBrainzConfig(bad)
    MusicBrainzConfig("music-dashboard/0.1 (https://example.invalid/contact)")


def test_cli_registry_command_and_limit_validation():
    parser = argparse.ArgumentParser()
    configure(parser.add_subparsers())
    args = parser.parse_args(["enrich-metadata", "--limit", "12"])
    assert args.handler is run and args.limit == 12 and not args.allow_network
    for limit in ("0", "1001"):
        with pytest.raises(SystemExit):
            parser.parse_args(["enrich-metadata", "--limit", limit])


def test_retry_after_http_dates_delta_and_invalid():
    now = datetime(2026, 11, 21, 12, tzinfo=UTC).timestamp()
    assert parse_retry_after({"retry-after": "Sat, 21 Nov 2026 12:01:00 GMT"}, now=now) == 60
    assert parse_retry_after({"retry-after": "Sat, 21 Nov 2026 11:00:00 GMT"}, now=now) == 0
    assert parse_retry_after({"retry-after": "15"}) == 15
    for bad in ("nan", "inf", "broken"):
        assert parse_retry_after({"retry-after": bad}, now=now) is None
    assert parse_retry_after({}) is None
    assert (
        rate_limit_exhausted_delay(
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(now + 25)}, now=now
        )
        == 25
    )
    assert (
        rate_limit_exhausted_delay(
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset-in": "7"}, now=now
        )
        == 7
    )


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("private URL"), TimeoutError("private URL"), IncompleteRead(b"partial")],
)
def test_network_and_truncated_http_are_transient_sanitized(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(MetadataTransientError) as caught:
        urllib_transport(HttpRequest("GET", "https://example.invalid/private"))
    assert str(caught.value) == "network error"


def test_stdlib_http_error_returns_status_without_logging(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid/private",
            503,
            "private",
            {"Retry-After": "45"},
            BytesIO(b"error"),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    result = urllib_transport(HttpRequest("GET", "https://example.invalid/private"))
    assert result.status == 503 and result.headers["retry-after"] == "45"
