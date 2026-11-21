"""Integration-edge CLI errors must not disclose arbitrary adapter exceptions."""

import argparse

import pytest

from music_dashboard.presentation.cli_commands.enrich import run


def test_enrichment_cli_sanitizes_adapter_failure(monkeypatch):
    def fail(limit):
        raise RuntimeError("postgresql://private-user:private-password@private-host/db")

    monkeypatch.setattr("music_dashboard.application.jobs.enrichment.run_once", fail)
    with pytest.raises(SystemExit) as caught:
        run(argparse.Namespace(allow_network=True, limit=1))
    message = str(caught.value)
    assert "MUSIC_METADATA_USER_AGENT" in message
    assert "private-" not in message
    assert caught.value.__suppress_context__
