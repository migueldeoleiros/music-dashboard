"""Public history: gate, opaque cursor, no timestamp leakage. Fake repository, no database."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from music_dashboard.application import public_history as app
from music_dashboard.config import settings
from music_dashboard.infrastructure.persistence.public_history_queries import (
    HistoryBatch,
    credit,
)
from music_dashboard.infrastructure.public_feed_cursors import OpaqueCursorStore
from music_dashboard.presentation.api.schemas.history import HistoryItem

REF = datetime(2026, 1, 1, 12, 34, 56, tzinfo=UTC)


class FakeRepo:
    def __init__(self):
        self.calls = []

    def fetch(self, tz, limit, reference=None, generation=None, boundary=None):
        self.calls.append((tz, limit, reference, generation, boundary))
        item = HistoryItem(title="t", artists=[credit("A", None)], group="Today")
        return HistoryBatch([item], "1:2", reference or REF, (REF, 7) if not boundary else None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "public_event_feeds_enabled", True)
    monkeypatch.setattr(settings, "fixture_mode", False)
    repo = FakeRepo()
    monkeypatch.setattr(app, "history_repository", lambda: repo)
    return repo


def test_disabled_and_fixture_fail_closed(monkeypatch):
    monkeypatch.setattr(app, "history_repository", lambda: pytest.fail("touched db"))
    for flag, fixture in ((False, False), (True, True)):
        monkeypatch.setattr(settings, "public_event_feeds_enabled", flag)
        monkeypatch.setattr(settings, "fixture_mode", fixture)
        with pytest.raises(HTTPException) as err:
            app.PublicHistory().page()
        assert err.value.status_code == 503


def test_cursor_is_opaque_pins_reference_and_leaks_no_time(enabled):
    first = app.PublicHistory().page(tz="Europe/Madrid", limit=1)
    dumped = first.model_dump_json()
    assert "2026" not in dumped and "12:34" not in dumped
    assert first.next_cursor and "2026" not in first.next_cursor and len(first.next_cursor) == 43
    second = app.PublicHistory().page(tz="Europe/Madrid", limit=1, cursor=first.next_cursor)
    assert enabled.calls[1][2:] == (REF, "1:2", (REF, 7))
    assert second.next_cursor is None


def test_cursor_rejects_garbage_changed_query_and_expiry(enabled):
    with pytest.raises(HTTPException) as err:
        app.PublicHistory().page(cursor="nope")
    assert err.value.status_code == 409
    token = app.PublicHistory().page(limit=1).next_cursor
    with pytest.raises(HTTPException) as err:
        app.PublicHistory().page(limit=2, cursor=token)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        app.PublicHistory().page(limit=101)
    assert err.value.status_code == 400


def test_store_is_bounded_and_expires():
    store = OpaqueCursorStore(ttl_seconds=60, max_entries=2)
    tokens = [store.issue(i) for i in range(3)]
    with pytest.raises(HTTPException):
        store.read(tokens[0])
    assert store.read(tokens[2]) == 2
    expired = OpaqueCursorStore(ttl_seconds=1e-9)
    with pytest.raises(HTTPException):
        expired.read(expired.issue("x"))


def test_credit_keys_never_guess():
    assert credit(" Ａ ", None).key == "name:a"
    assert credit("  ", None).key is None
