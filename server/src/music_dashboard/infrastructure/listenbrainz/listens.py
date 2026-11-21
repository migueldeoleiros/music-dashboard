"""ListenBrainz listens/export adapter (``ListenBrainzClientPort`` minus metadata).

Constructor::

    ListenBrainzClient(transport, *, poll_interval=5.0, max_polls=120,
                       max_zip_bytes=64 MiB, max_unzipped_bytes=256 MiB, max_members=1000)

``transport`` is a ``ListenBrainzTransport`` (token supplied there, worker only). Keep the
64 MiB compressed / 256 MiB uncompressed defaults.

Methods: ``page(user, *, max_ts=None, min_ts=None, count=1000) -> list[dict]``,
``listen_count(user) -> int``, ``export(user, *, min_records=None) -> ExportSnapshot``.
Listen dicts are returned untouched (``track_metadata``/``mbid_mapping`` preserved).

Supported response shapes (assumptions; deployed export shape is unverified, no real smoke):
* page:   ``GET /1/user/{user}/listens?count=N[&max_ts=T|&min_ts=T]`` ->
          ``{"payload": {"listens": [ {...}, ... ]}}``
* count:  ``GET /1/user/{user}/listen-count`` -> ``{"payload": {"count": <int>}}``
* token owner: ``GET /1/validate-token`` (authenticated) ->
          ``{"valid": true, "user_name": "<owner>"}``; must be valid and the owner must equal
          (casefold) the requested user, else ``ExportAccountMismatchError`` and nothing is
          requested. Checked first, always.
* export create: ``POST /1/export/`` (authenticated, no body) ->
          ``{"export_id": <int|str>}`` (``"id"`` accepted as alias)
* export status: ``GET /1/export/{id}`` (authenticated) -> ``{"status": <str>, ...}``.
          Ready ONLY when status (case-insensitive) is ``complete``/``completed``/``ready``.
          ``failed``/``error``/``expired``/``deleted`` abort; every other status keeps polling
          up to ``max_polls`` (then ``ExportTimeoutError``). Optional snapshot manifest:
          ``"listen_count": <int >= 0>`` in the ready status (synthetic/assumed field name).
* download: status ``download_url`` (absolute https, host validated by the transport) if
          present, else ``GET /1/export/download/{id}`` (authenticated); body is a ZIP.
* ZIP: members named ``listens*.jsonl`` (any directory) are read; if none, a single ``*.jsonl``
          member is used; otherwise error. Each non-blank line must be a JSON object.

Result: ``ExportSnapshot(rows, user, expected_count, complete)``.
* ``complete`` is True ONLY with the account check passed, explicit ready status and a trusted
  snapshot ``listen_count`` manifest; otherwise False (rows usable for import, but callers must
  not infer deletions). The pre-export global ``listen_count`` is supplemental only.
* ``rows`` is a single-pass lazy generator (no list is materialised). Validation completes only
  when it is EXHAUSTED: consumers must fully consume/stage it before activating anything.
  It raises ``ExportCorruptError`` (bad CRC reached at member EOF, bad JSON/non-object/
  undecodable line, duplicate ``(listened_at, recording_msid)`` identity) or
  ``ExportIncompleteError`` (no rows; unique valid + invalid-identity rows != manifest; fewer
  rows than the pre-export count). Rows without a valid identity are yielded (for raw
  quarantine) and counted toward the manifest; ``complete`` stays True, so the repository
  remains the final identity safety check (it must block deletions when any are present).

Resource bounds (defaults 64 MiB compressed / 256 MiB uncompressed):
download body <= ``max_zip_bytes``; archive member count <= ``max_members``
and the sum of ALL members' declared uncompressed sizes <= ``max_unzipped_bytes``, both checked
BEFORE any decompression; no ``testzip`` pass (CRC is verified while streaming the selected
member to EOF). The compressed archive stays in memory; identities of one export are held in a
set for duplicate detection (memory ~ rows, not payload).
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlencode
from zlib import error as zlib_error

from music_dashboard.domain.listening.entities import ExportSnapshot
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


class ExportAccountMismatchError(ExportError):
    pass


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
        max_zip_bytes: int = 64 << 20,
        max_unzipped_bytes: int = 256 << 20,
        max_members: int = 1000,
    ) -> None:
        if max_polls < 1 or poll_interval < 0:
            raise ValueError("max_polls must be >= 1 and poll_interval >= 0")
        self._t = transport
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._max_zip = max_zip_bytes
        self._max_unzipped = max_unzipped_bytes
        self._max_members = max_members

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

    def export(self, user: str, *, min_records: int | None = None) -> ExportSnapshot:
        self._user(user)
        self._verify_owner(user)
        if min_records is None:
            min_records = self.listen_count(user)  # supplemental only
        elif not _is_int(min_records) or min_records < 0:
            raise ValueError("min_records must be a non-negative integer")
        created = self._t.request("POST", "/1/export/", authenticated=True)
        export_id = self._export_id(created)
        status = self._wait_ready(export_id)
        manifest = status.get("listen_count")
        expected = (
            manifest
            if isinstance(manifest, int) and not isinstance(manifest, bool) and manifest >= 0
            else None
        )
        target = status.get("download_url")
        if target is None:
            target = f"/1/export/download/{export_id}"
        elif not isinstance(target, str):
            raise ProtocolError("download_url malformed")
        blob = self._t.download(target, authenticated=True)
        if len(blob) > self._max_zip:
            raise ExportCorruptError("export archive exceeds size limit")
        zf, info = self._open_zip(blob)
        return ExportSnapshot(
            rows=self._rows(zf, info, expected, min_records),
            user=user,
            expected_count=expected,
            complete=expected is not None,
        )

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

    def _verify_owner(self, user: str) -> None:
        data = self._t.request("GET", "/1/validate-token", authenticated=True)
        owner = data.get("user_name")
        if data.get("valid") is not True or not isinstance(owner, str) or not owner:
            raise ExportAccountMismatchError("token ownership could not be validated")
        if owner.casefold() != user.casefold():
            raise ExportAccountMismatchError("token does not belong to the configured user")

    def _open_zip(self, blob: bytes) -> tuple[zipfile.ZipFile, zipfile.ZipInfo]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except (zipfile.BadZipFile, EOFError, zlib_error):
            raise ExportCorruptError("export archive is corrupt") from None
        try:
            infos = zf.infolist()
            if len(infos) > self._max_members:
                raise ExportCorruptError("export archive has too many members")
            if sum(i.file_size for i in infos) > self._max_unzipped:
                raise ExportCorruptError("export archive exceeds uncompressed size limit")
            jsonl = [i for i in infos if not i.is_dir() and i.filename.lower().endswith(".jsonl")]
            chosen = [
                i for i in jsonl if i.filename.rsplit("/", 1)[-1].lower().startswith("listens")
            ]
            chosen = chosen or (jsonl if len(jsonl) == 1 else [])
            if len(chosen) != 1:
                raise ExportCorruptError("export archive has no single identifiable listens JSONL")
            return zf, chosen[0]
        except BaseException:
            zf.close()
            raise

    def _rows(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        expected: int | None,
        min_records: int,
    ) -> Iterator[dict[str, Any]]:
        seen: set[tuple[int, uuid.UUID]] = set()
        invalid = 0
        try:
            with zf, zf.open(info) as raw:
                for line in io.TextIOWrapper(raw, encoding="utf-8", errors="strict"):
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        raise ExportCorruptError("export contains invalid JSON") from None
                    if not isinstance(obj, dict):
                        raise ExportCorruptError("export line is not a JSON object")
                    ident = _identity(obj)
                    if ident is None:
                        invalid += 1
                    elif ident in seen:
                        raise ExportCorruptError("export contains duplicate listen identity")
                    else:
                        seen.add(ident)
                    yield obj
        except ExportError:
            raise
        except (zipfile.BadZipFile, UnicodeDecodeError, EOFError, zlib_error):
            raise ExportCorruptError("export archive is corrupt") from None
        # reached only after the member was read to EOF (CRC verified)
        if not seen and not invalid:
            raise ExportIncompleteError("export contains no listens")
        received = len(seen) + invalid
        if expected is not None and received != expected:
            raise ExportIncompleteError(
                f"export has {received} listens (unique valid + invalid identity), "
                f"manifest says {expected}"
            )
        if received < min_records:
            raise ExportIncompleteError(
                f"export has {received} listens, expected at least {min_records}"
            )


def _identity(obj: dict[str, Any]) -> tuple[int, uuid.UUID] | None:
    ts = obj.get("listened_at")
    metadata = obj.get("track_metadata")
    info = metadata.get("additional_info") if isinstance(metadata, dict) else None
    msid = obj.get("recording_msid", info.get("recording_msid") if isinstance(info, dict) else None)
    if type(ts) is not int or not 0 <= ts <= 253402300799 or not isinstance(msid, str):
        return None
    try:
        parsed = uuid.UUID(msid.strip())
    except ValueError:
        return None
    return (ts, parsed) if parsed.int else None
