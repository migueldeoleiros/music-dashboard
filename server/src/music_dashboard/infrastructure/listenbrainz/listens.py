"""ListenBrainz listens/export adapter (``ListenBrainzClientPort`` minus metadata).

Constructor::

    ListenBrainzClient(transport, *, poll_interval=5.0, max_polls=120,
                       max_zip_bytes=1 << 30, max_unzipped_bytes=4 << 30)

``transport`` is a ``ListenBrainzTransport`` (token supplied there, worker only).

Methods: ``page(user, *, max_ts=None, min_ts=None, count=1000) -> list[dict]``,
``listen_count(user) -> int``, ``export(user, *, min_records=None) -> list[dict]``.
Listen dicts are returned untouched (``track_metadata``/``mbid_mapping`` preserved).

Supported response shapes (assumptions; deployed export shape is unverified, no real smoke):
* page:   ``GET /1/user/{user}/listens?count=N[&max_ts=T|&min_ts=T]`` ->
          ``{"payload": {"listens": [ {...}, ... ]}}``
* count:  ``GET /1/user/{user}/listen-count`` -> ``{"payload": {"count": <int>}}``
* export create: ``POST /1/export/`` (authenticated, no body) ->
          ``{"export_id": <int|str>}`` (``"id"`` accepted as alias)
* export status: ``GET /1/export/{id}`` (authenticated) -> ``{"status": <str>, ...}``.
          Ready ONLY when status (case-insensitive) is ``complete``/``completed``/``ready``.
          ``failed``/``error``/``expired``/``deleted`` abort; every other status
          (``waiting``, ``in_progress``, unknown, missing) keeps polling up to ``max_polls``.
* download: status ``download_url`` (absolute https, host validated by the transport) if
          present, else ``GET /1/export/download/{id}`` (authenticated); body is a ZIP.
* ZIP: members named ``listens*.jsonl`` (any directory) are read; if none, a single ``*.jsonl``
          member is used; otherwise error. Each non-blank line must be a JSON object.

Completeness: the ZIP must pass CRC verification, every line must parse, at least one record
must exist (an empty export always raises), and the record count must be >= ``min_records``
(default: ``listen_count`` read before the export is requested; deletions upstream can make
a legitimately smaller export fail safe, callers may pass an explicit lower bound). Nothing is
returned until the whole archive is validated; the result is a fully materialised list, so
consumers can stage it completely before activation. Count alone is NOT proof of completeness.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any
from urllib.parse import quote, urlencode
from zlib import error as zlib_error

from music_dashboard.infrastructure.listenbrainz.transport import (
    ListenBrainzError,
    ListenBrainzTransport,
    ProtocolError,
)

MAX_PAGE_COUNT = 1000
_READY = frozenset({"complete", "completed", "ready"})
_FAILED = frozenset({"failed", "error", "expired", "deleted"})
_EXPORT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ExportError(ListenBrainzError):
    """Export did not produce a complete, valid snapshot. Nothing may be activated."""


class ExportFailedError(ExportError):
    pass


class ExportTimeoutError(ExportError):
    pass


class ExportCorruptError(ExportError):
    pass


class ExportIncompleteError(ExportError):
    pass


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class ListenBrainzClient:
    def __init__(
        self,
        transport: ListenBrainzTransport,
        *,
        poll_interval: float = 5.0,
        max_polls: int = 120,
        max_zip_bytes: int = 1 << 30,
        max_unzipped_bytes: int = 4 << 30,
    ) -> None:
        if max_polls < 1 or poll_interval < 0:
            raise ValueError("max_polls must be >= 1 and poll_interval >= 0")
        self._t = transport
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._max_zip = max_zip_bytes
        self._max_unzipped = max_unzipped_bytes

    # ---- API --------------------------------------------------------------------------
    def page(
        self,
        user: str,
        *,
        max_ts: int | None = None,
        min_ts: int | None = None,
        count: int = MAX_PAGE_COUNT,
    ) -> list[dict[str, Any]]:
        if max_ts is not None and min_ts is not None:
            raise ValueError("only one of max_ts/min_ts is supported")
        if not _is_int(count) or not 1 <= count <= MAX_PAGE_COUNT:
            raise ValueError(f"count must be an integer in 1..{MAX_PAGE_COUNT}")
        for name, bound in (("max_ts", max_ts), ("min_ts", min_ts)):
            if bound is not None and (not _is_int(bound) or bound < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        params: dict[str, int] = {"count": count}
        if max_ts is not None:
            params["max_ts"] = max_ts
        if min_ts is not None:
            params["min_ts"] = min_ts
        data = self._t.request("GET", f"/1/user/{self._user(user)}/listens?{urlencode(params)}")
        listens = self._payload(data).get("listens")
        if not isinstance(listens, list) or not all(isinstance(x, dict) for x in listens):
            raise ProtocolError("listens payload malformed")
        return listens

    def listen_count(self, user: str) -> int:
        data = self._t.request("GET", f"/1/user/{self._user(user)}/listen-count")
        count = self._payload(data).get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ProtocolError("listen-count payload malformed")
        return count

    def export(self, user: str, *, min_records: int | None = None) -> list[dict[str, Any]]:
        self._user(user)
        if min_records is None:
            min_records = self.listen_count(user)
        elif not _is_int(min_records) or min_records < 0:
            raise ValueError("min_records must be a non-negative integer")
        created = self._t.request("POST", "/1/export/", authenticated=True)
        export_id = self._export_id(created)
        status = self._wait_ready(export_id)
        target = status.get("download_url")
        if target is None:
            target = f"/1/export/download/{export_id}"
        elif not isinstance(target, str):
            raise ProtocolError("download_url malformed")
        blob = self._t.download(target, authenticated=True)
        if len(blob) > self._max_zip:
            raise ExportCorruptError("export archive exceeds size limit")
        records = self._parse_zip(blob)
        if not records:
            raise ExportIncompleteError("export contains no listens")
        if len(records) < min_records:
            raise ExportIncompleteError(
                f"export has {len(records)} listens, expected at least {min_records}"
            )
        return records

    # ---- helpers ----------------------------------------------------------------------
    @staticmethod
    def _user(user: str) -> str:
        if not isinstance(user, str) or not user.strip():
            raise ValueError("user must be a non-empty string")
        return quote(user, safe="")

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("missing payload object")
        return payload

    @staticmethod
    def _export_id(data: dict[str, Any]) -> str:
        raw = data.get("export_id", data.get("id"))
        if not (_is_int(raw) or isinstance(raw, str)) or not _EXPORT_ID.match(str(raw)):
            raise ProtocolError("export id missing or malformed")
        return str(raw)

    def _wait_ready(self, export_id: str) -> dict[str, Any]:
        for poll in range(self._max_polls):
            status = self._t.request("GET", f"/1/export/{export_id}", authenticated=True)
            state = status.get("status")
            state = state.strip().lower() if isinstance(state, str) else ""
            if state in _READY:
                return status
            if state in _FAILED:
                raise ExportFailedError(f"export ended with status {state!r}")
            if poll + 1 < self._max_polls:
                self._t.sleep(self._poll_interval)
        raise ExportTimeoutError("export not ready within polling budget")

    def _parse_zip(self, blob: bytes) -> list[dict[str, Any]]:
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                if zf.testzip() is not None:
                    raise ExportCorruptError("export archive failed CRC check")
                infos = [i for i in zf.infolist() if not i.is_dir()]
                jsonl = [i for i in infos if i.filename.lower().endswith(".jsonl")]
                chosen = [
                    i for i in jsonl if i.filename.rsplit("/", 1)[-1].lower().startswith("listens")
                ] or (jsonl if len(jsonl) == 1 else [])
                if not chosen:
                    raise ExportCorruptError("export archive has no identifiable listens JSONL")
                if sum(i.file_size for i in chosen) > self._max_unzipped:
                    raise ExportCorruptError("export archive exceeds uncompressed size limit")
                records: list[dict[str, Any]] = []
                for info in chosen:
                    with zf.open(info) as raw:
                        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="strict"):
                            if not line.strip():
                                continue
                            try:
                                obj = json.loads(line)
                            except ValueError:
                                raise ExportCorruptError("export contains invalid JSON") from None
                            if not isinstance(obj, dict):
                                raise ExportCorruptError("export line is not a JSON object")
                            records.append(obj)
                return records
        except ExportError:
            raise
        except (zipfile.BadZipFile, UnicodeDecodeError, EOFError, zlib_error):
            raise ExportCorruptError("export archive is corrupt") from None
