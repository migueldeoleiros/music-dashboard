"""Worker registry host: discovery, logs, isolation; fully mocked, socket-forbidden."""

import logging
import socket
from threading import Event
from unittest.mock import Mock

import pytest

from music_dashboard import worker


@pytest.fixture
def socket_forbidden(monkeypatch):
    class Blocked(Exception):
        pass

    def forbid(*args, **kwargs):
        raise Blocked("network forbidden in worker host tests")

    monkeypatch.setattr(socket, "socket", forbid)
    monkeypatch.setattr(socket, "create_connection", forbid)
    return Blocked


def registry_names() -> list[str]:
    import pkgutil

    return sorted(
        item.name
        for item in pkgutil.iter_modules(worker.jobs.__path__)
        if not item.name.startswith("_")
    )


def run_main_once(monkeypatch, tick_for):
    """Run worker.main with real job imports; ticks are spied in place.

    Event/signals are mocked so exactly one loop iteration runs. Each spy
    keeps the production tick ``__module__`` so the startup log verifies real
    registry names. Returns (imported module names, tick calls, wait args).
    """
    waits = []
    event = Mock(wait=Mock(side_effect=waits.append))
    # is_set() is False before the first wait, then True — one deterministic loop.
    event.is_set.side_effect = lambda: len(waits) > 0
    monkeypatch.setattr(worker, "Event", lambda: event)
    monkeypatch.setattr(worker.signal, "signal", lambda *args: None)

    imports, calls = [], []
    for name in registry_names():
        module = worker.importlib.import_module(f"{worker.jobs.__name__}.{name}")
        imports.append(f"{worker.jobs.__name__}.{name}")

        def spy(n=name):
            calls.append(n)
            tick_for(n)

        spy.__module__ = f"{worker.jobs.__name__}.{name}"  # production log name
        monkeypatch.setattr(module, "tick", spy)
    worker.main()
    return imports, calls, waits


def test_main_discovers_both_jobs_logs_names_and_ticks_once(monkeypatch, socket_forbidden, caplog):
    caplog.set_level(logging.INFO)
    ticks = []
    imports, calls, waits = run_main_once(monkeypatch, ticks.append)
    assert imports == [f"music_dashboard.application.jobs.{name}" for name in registry_names()]
    required = {f"music_dashboard.application.jobs.{name}" for name in ("enrichment", "sync")}
    assert required <= set(imports)
    assert calls == registry_names()  # every discovered tick ran exactly once
    assert waits == [30]  # Event.wait(30) called once; loop ended after one iteration
    startup = [
        record
        for record in caplog.records
        if record.levelname == "INFO" and "registered jobs" in record.message
    ]
    assert len(startup) == 1
    assert ", ".join(imports) in startup[0].message, startup[
        0
    ].message  # log names match production tick __module__ values


def test_main_failing_job_does_not_crash_or_block_next(monkeypatch, socket_forbidden, caplog):
    def ticks(name):
        if name == "enrichment":  # first job in the loop fails
            raise ValueError("synthetic-test-token in an unsafe upstream exception")

    imports, calls, waits = run_main_once(monkeypatch, ticks)
    assert any(i.endswith(".enrichment") for i in imports) and imports[-1].endswith(".sync")
    assert calls == registry_names()  # sync still ticked after enrichment failed
    assert waits == [30]
    errors = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert "synthetic-test-token" not in caplog.text


def test_fixture_ticks_run_offline_without_socket(socket_forbidden, monkeypatch):
    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "true")
    monkeypatch.delenv("LISTENBRAINZ_TOKEN", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_USERNAME", raising=False)
    monkeypatch.delenv("MUSIC_METADATA_USER_AGENT", raising=False)

    enrichment = __import__("music_dashboard.application.jobs.enrichment", fromlist=["tick"])
    enrichment.tick()  # fixture mode: deliberate no-op

    sync = __import__("music_dashboard.application.jobs.sync", fromlist=["tick"])
    assert sync.tick() is None  # scheduled fixture sync returns fixture-idle
    assert sync.run_sync("incremental", scheduled=True) == {"status": "fixture-idle"}


def test_real_sync_without_token_is_actionable_not_secret(monkeypatch, socket_forbidden):
    from music_dashboard.application.jobs import sync as job

    monkeypatch.setenv("MUSIC_FIXTURE_MODE", "false")
    monkeypatch.delenv("LISTENBRAINZ_TOKEN", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_USERNAME", raising=False)
    with pytest.raises(job.SyncConfigurationError) as error:
        job.run_sync("incremental", scheduled=False)
    message = str(error.value)
    assert "LISTENBRAINZ_TOKEN" in message and "MUSIC_FIXTURE_MODE=true" in message
    assert "synthetic-test-token" not in message


def test_fixture_settings_are_tokenless(monkeypatch):
    from music_dashboard.application.jobs import sync as job

    monkeypatch.delenv("MUSIC_FIXTURE_MODE", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_TOKEN", raising=False)
    monkeypatch.delenv("LISTENBRAINZ_USERNAME", raising=False)
    settings = job.WorkerSyncSettings.from_env()
    assert settings.fixture_mode and settings.token == ""


def test_worker_module_imports_never_use_sockets():
    # Discovery modules are imported at collection time before any test blocks
    # sockets; Event remains the real threading type, proving safe import surface.
    assert worker.Event is Event
    names = registry_names()
    assert {"enrichment", "sync"} <= set(names)
    assert all(not name.startswith("_") for name in names)
