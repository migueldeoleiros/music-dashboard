"""Regression tests built from SANITIZED SYNTHETIC fixtures that mimic the verified real shapes.

Verified real facts: ``GET /1/export/{id}`` -> ``status: "completed"`` and no manifest;
download at ``/1/export/{id}/download``; ZIP = ``user.json`` + ``listens/YYYY/MM.jsonl`` +
``feedback.jsonl``; ``listened_at`` is a float (``1790856000.0``); ``mbid_mapping`` may be null.
No real identifiers or payload values appear here.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from music_dashboard.application.reconcile_history import reconcile_history
from music_dashboard.domain.listening.entities import epoch_seconds
from music_dashboard.infrastructure.listenbrainz.listens import (
    ExportCorruptError,
    ExportIncompleteError,
    ListenBrainzClient,
)
from music_dashboard.infrastructure.listenbrainz.transport import (
    HttpRequest,
    HttpResponse,
    ListenBrainzTransport,
)

USER = "Example_User"
NOW = datetime(2026, 11, 22, tzinfo=UTC)
BASE_TS = 1790856000


def msid(n: int) -> str:
    return str(uuid.UUID(int=n))


def real_listen(n: int, ts: Any = None, mapped: bool = True) -> dict[str, Any]:
    ts = float(BASE_TS + n) if ts is None else ts
    mapping = (
        {
            "recording_name": f"Track {n}",
            "recording_mbid": str(uuid.UUID(int=10_000 + n)),
            "release_mbid": str(uuid.UUID(int=20_000 + n)),
            "artist_mbids": [str(uuid.UUID(int=30_000 + n))],
            "caa_id": None,
            "caa_release_mbid": None,
            "artists": [],
        }
        if mapped
        else None
    )
    return {
        "inserted_at": BASE_TS + n,
        "listened_at": ts,
        "recording_msid": msid(n),
        "track_metadata": {
            "track_name": f"Track {n}",
            "artist_name": "Artist",
            "release_name": "Release",
            "additional_info": {},
            "mbid_mapping": mapping,
        },
    }


def jsonl(rows: Iterable[Any]) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def real_zip(*months: tuple[str, str], username: str = USER) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("user.json", json.dumps({"user_id": 4242, "username": username}))
        zf.writestr("feedback.jsonl", '{"recording_msid": "x", "score": 1}\n')
        for name, body in months:
            zf.writestr(name, body)
    return buf.getvalue()


REAL_STATUS = {
    "available_until": 1790999999,
    "created": 1790856000,
    "end_time": 1790856010,
    "export_id": 5,
    "filename": "listenbrainz-export.zip",
    "progress": "Export completed",
    "start_time": 1790856001,
    "status": "completed",
    "type": "export_all_user_data",
}


class Http:
    def __init__(self, blob: bytes, count: int) -> None:
        self.requests: list[HttpRequest] = []
        self.script = {
            "/1/validate-token": {"valid": True, "user_name": USER.lower()},
            f"/1/user/{USER}/listen-count": {"payload": {"count": count}},
            "/1/export/": {"export_id": 5},
            "/1/export/5": REAL_STATUS,
        }
        self.blob = blob

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = request.url.split("api.listenbrainz.org", 1)[1]
        if path == "/1/export/5/download":
            return HttpResponse(200, {"Content-Type": "application/zip"}, self.blob)
        if path in self.script:
            return HttpResponse(200, {}, json.dumps(self.script[path]).encode())
        return HttpResponse(404, {}, b"")  # e.g. the old, wrong download URL

    @property
    def paths(self) -> list[str]:
        return [r.url.split("api.listenbrainz.org", 1)[1] for r in self.requests]


def client(http: Http) -> ListenBrainzClient:
    transport = ListenBrainzTransport(http, token="synthetic", sleep=lambda s: None)
    return ListenBrainzClient(transport, poll_interval=0)


def three_months(rows: list[dict[str, Any]]) -> bytes:
    return real_zip(
        ("listens/2026/09.jsonl", jsonl(rows[:1])),
        ("listens/2026/10.jsonl", jsonl(rows[1:2])),
        ("listens/2026/11.jsonl", jsonl(rows[2:])),
    )


# ------------------------------------------------------------------ adapter


def test_real_flow_uses_correct_download_url_and_never_the_old_one() -> None:
    rows = [real_listen(1), real_listen(2, mapped=False), real_listen(3)]
    http = Http(three_months(rows), count=3)
    snap = client(http).export(USER)
    got = list(snap.rows)
    assert got == rows  # raw preserved verbatim, null and non-null mbid_mapping
    assert http.paths == [
        "/1/validate-token",
        f"/1/user/{USER}/listen-count",
        "/1/export/",
        "/1/export/5",
        "/1/export/5/download",
    ]
    assert not any("/export/download/" in p for p in http.paths)
    # real status carries no manifest: import-complete evidence only, never deletion-safe
    assert snap.complete is False and snap.expected_count is None
    assert snap.lower_bound == 3
    assert http.requests[-1].headers["Authorization"] == "Token synthetic"


def test_rows_may_exceed_listen_count_lower_bound() -> None:
    rows = [real_listen(1), real_listen(2)]
    snap = client(Http(three_months(rows + [real_listen(3)]), count=2)).export(USER)
    assert len(list(snap.rows)) == 3


def test_fewer_rows_than_lower_bound_fails_on_exhaustion() -> None:
    snap = client(Http(three_months([real_listen(1), real_listen(2)]), count=3)).export(USER)
    with pytest.raises(ExportIncompleteError, match="at least 3"):
        list(snap.rows)


def test_account_is_checked_via_user_json_username_casefold() -> None:
    blob = real_zip(("listens/2026/09.jsonl", jsonl([real_listen(1)])), username=USER.upper())
    assert list(client(Http(blob, count=1)).export(USER).rows)
    other = real_zip(("listens/2026/09.jsonl", jsonl([real_listen(1)])), username="someone_else")
    from music_dashboard.infrastructure.listenbrainz.listens import ExportAccountMismatchError

    with pytest.raises(ExportAccountMismatchError):
        client(Http(other, count=1)).export(USER)


def test_feedback_and_unknown_members_are_ignored() -> None:
    blob = real_zip(
        ("listens/2026/09.jsonl", jsonl([real_listen(1)])),
        ("feedback.jsonl", "this is not json\n"),
        ("something/else.jsonl", "neither is this\n"),
        ("listens/README.txt", "ignored"),
        ("../listens/evil.jsonl", "ignored"),
    )
    assert [r["recording_msid"] for r in client(Http(blob, count=1)).export(USER).rows] == [msid(1)]


# ------------------------------------------------------------- timestamps


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1790856000, 1790856000),
        (1790856000.0, 1790856000),
        (0, 0),
        (0.0, 0),
        (253402300799, 253402300799),
        (1790856000.5, None),
        (-1, None),
        (-1.0, None),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
        (1e300, None),
        (253402300800, None),
        (True, None),
        ("1790856000", None),
        (None, None),
    ],
)
def test_epoch_seconds(value: object, expected: int | None) -> None:
    got = epoch_seconds(value)
    assert got == expected
    assert got is None or type(got) is int


def raw_line(ts_text: str, n: int = 1) -> str:
    return json.dumps(real_listen(n, ts="@@")).replace('"@@"', ts_text) + "\n"


@pytest.mark.parametrize(
    "ts_text", ["1790856000.0", "1790856000", "1.790856e9", "1790856000.000", "1790856000E0"]
)
def test_exact_integral_numeric_forms_keep_identity(ts_text: str) -> None:
    blob = real_zip(("listens/2026/09.jsonl", raw_line(ts_text)))
    (row,) = list(client(Http(blob, count=1)).export(USER).rows)
    assert epoch_seconds(row["listened_at"]) == BASE_TS


@pytest.mark.parametrize(
    "ts_text",
    [
        "1790856000.5",
        "1790856000.0000000001",  # would silently round to ...000.0 as a float
        "NaN",
        "Infinity",
        "-Infinity",
        "-5.0",
        "1e999",
        "1e300",
    ],
)
def test_unsafe_numeric_forms_never_become_an_identity(ts_text: str) -> None:
    good = real_listen(2)
    blob = real_zip(("listens/2026/09.jsonl", raw_line(ts_text) + jsonl([good])))
    snap = client(Http(blob, count=1)).export(USER)
    bad, ok = list(snap.rows)
    assert epoch_seconds(bad["listened_at"]) is None  # quarantined downstream, not rounded
    assert epoch_seconds(ok["listened_at"]) == BASE_TS + 2
    json.dumps(bad, allow_nan=False)  # still spoolable


def test_float_and_int_spellings_of_one_listen_are_a_duplicate() -> None:
    a = real_listen(1)
    b = dict(a, listened_at=BASE_TS + 1)
    blob = real_zip(("listens/2026/09.jsonl", jsonl([a])), ("listens/2026/10.jsonl", jsonl([b])))
    with pytest.raises(ExportCorruptError, match="duplicate"):
        list(client(Http(blob, count=0)).export(USER).rows)


# -------------------------------------------------------- reconcile policy


class Repo:
    """Records what the use case asked for; mimics the real repository's blocking rule."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.runs: list[dict[str, Any]] = []
        self.activations: list[dict[str, Any]] = []

    @contextmanager
    def locked(self, user: str):  # type: ignore[no-untyped-def]
        yield True

    def begin_run(self, user: str, kind: str, stats: Any = None) -> int:
        self.runs.append({"kind": kind, "status": "running"})
        return len(self.runs) - 1

    def finish_run(self, run_id: int, status: str, stats: Any, error: Any = None) -> None:
        self.runs[run_id].update(status=status, stats=stats, error=error)

    def save_checkpoint(self, user: str, state: dict[str, Any]) -> None:
        self.state.update(state)

    def activate_snapshot(
        self,
        user: str,
        rows: Iterable[dict[str, Any]],
        snapshot_at: datetime,
        allow_large_delete: bool = False,
        snapshot_complete: bool = False,
    ) -> dict[str, int]:
        data = list(rows)
        self.activations.append({"rows": data, "snapshot_complete": snapshot_complete})
        return {
            "rows": len(data),
            "invalid": 0,
            "changed": len(data),
            "tombstoned": 0,
            "deletions_blocked": int(not snapshot_complete),
        }


def test_import_complete_succeeds_advances_checkpoint_and_never_deletes() -> None:
    rows = [real_listen(n) for n in (1, 2, 3)]
    repo = Repo()
    stats = reconcile_history(
        client(Http(three_months(rows), count=3)),
        repo,
        USER,
        now=NOW,
        bootstrap=True,  # type: ignore[arg-type]
    )
    (activation,) = repo.activations
    assert activation["snapshot_complete"] is False  # no deletion authority
    assert stats["deletions_blocked"] == 1 and stats["tombstoned"] == 0
    assert stats["import_complete"] == 1 and stats["deletion_safe"] == 0
    assert repo.runs[0]["status"] == "succeeded"
    assert repo.state["last_export_at"] == NOW and repo.state["requires_reconcile"] is False
    # float timestamps survive the spool unchanged (identity input is exact)
    assert [epoch_seconds(r["listened_at"]) for r in activation["rows"]] == [
        BASE_TS + 1,
        BASE_TS + 2,
        BASE_TS + 3,
    ]


def test_fewer_rows_than_lower_bound_fails_before_anything_is_activated() -> None:
    repo = Repo()
    with pytest.raises(ExportIncompleteError):
        reconcile_history(
            client(Http(three_months([real_listen(1), real_listen(2)]), count=3)),  # type: ignore[arg-type]
            repo,
            USER,
            now=NOW,
        )
    assert repo.activations == [] and repo.state == {}
    assert repo.runs[0]["status"] == "failed"


def test_trusted_manifest_remains_the_only_deletion_authority() -> None:
    class Manifest(Http):
        def __init__(self, blob: bytes, count: int) -> None:
            super().__init__(blob, count)
            self.script["/1/export/5"] = {**REAL_STATUS, "listen_count": 3}

    rows = [real_listen(n) for n in (1, 2, 3)]
    repo = Repo()
    stats = reconcile_history(client(Manifest(three_months(rows), 3)), repo, USER, now=NOW)  # type: ignore[arg-type]
    assert repo.activations[0]["snapshot_complete"] is True
    assert stats["deletion_safe"] == 1
