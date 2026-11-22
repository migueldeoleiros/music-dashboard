"""Opt-in public refresh: same-origin POST admission, durable correlated status.

State lives in one database row behind two SECURITY DEFINER functions; the API role has no
table grants. Admission writes, so it runs in its own transaction beginning with
SET TRANSACTION READ WRITE (the API role is read-only by default). Behind a proxy the
Origin check compares against the Host header and the scheme Starlette sees; the proxy must
forward the original Host (with port) and, for TLS termination, scheme must be restored by a
trusted proxy-headers configuration (never trust X-Forwarded-Proto from arbitrary clients).
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from music_dashboard.config import settings
from music_dashboard.infrastructure.persistence.database import create_db_engine

router = APIRouter(prefix="/api", tags=["refresh"])
NO_STORE = {"Cache-Control": "no-store"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_IN_FLIGHT = {"pending", "running", "deferred"}


class RefreshStatus(BaseModel):
    enabled: bool
    request_id: str | None = None
    status: Literal["idle", "pending", "running", "succeeded", "failed", "expired", "deferred"]
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cooldown_until: datetime | None = None
    retry_after_seconds: int | None = None
    coalesced: bool = False
    detail: str | None = None


class RefreshError(BaseModel):
    code: str
    message: str
    details: list[str] = []


def refresh_enabled() -> bool:
    return settings.public_refresh_enabled and not settings.fixture_mode


@lru_cache(maxsize=1)
def refresh_engine() -> Engine:
    return create_db_engine(settings.database_url)


def _origin_tuple(scheme: str, netloc: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(f"{scheme}://{netloc}")
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host or parts.username or parts.password:
        return None
    return scheme.lower(), host.lower(), port or _DEFAULT_PORTS.get(scheme.lower())


def same_origin(request: Request) -> bool:
    """Strict: Origin must be present and equal scheme/host/port of this request."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return False
    if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin"}:
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in _DEFAULT_PORTS or parts.path not in {"", "/"}:
        return False
    if parts.query or parts.fragment:
        return False
    expected = _origin_tuple(request.url.scheme, host)
    actual = _origin_tuple(parts.scheme, parts.netloc)
    return expected is not None and expected == actual


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=RefreshError(code=code, message=message).model_dump(),
        headers=NO_STORE,
    )


def _payload(row: Any | None, *, coalesced: bool = False) -> RefreshStatus:
    if row is None:
        return RefreshStatus(enabled=True, status="idle")
    now: datetime = row["server_now"]
    cooldown = row["cooldown_until"]
    remaining = (
        max(1, math.ceil((cooldown - now).total_seconds()))
        if cooldown is not None and cooldown > now
        else None
    )
    return RefreshStatus(
        enabled=True,
        request_id=str(row["request_id"]) if row["request_id"] else None,
        status=row["status"],
        requested_at=row["requested_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        cooldown_until=cooldown,
        retry_after_seconds=remaining,
        coalesced=coalesced,
        detail=row["detail"],
    )


def _respond(model: RefreshStatus, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=model.model_dump(mode="json"), headers=NO_STORE
    )


@router.get(
    "/refresh",
    response_model=RefreshStatus,
    operation_id="getRefreshStatus",
    responses={503: {"model": RefreshError}},
)
def get_refresh() -> Any:
    if not refresh_enabled():
        return _respond(RefreshStatus(enabled=False, status="idle"), 200)
    try:
        with refresh_engine().connect() as conn:
            row = (
                conn.execute(text("SELECT * FROM public.public_refresh_status()"))
                .mappings()
                .first()
            )
    except SQLAlchemyError:
        return _error(503, "unavailable", "Refresh status temporarily unavailable")
    return _respond(_payload(row), 200)


@router.post(
    "/refresh",
    response_model=RefreshStatus,
    status_code=202,
    operation_id="requestRefresh",
    responses={
        200: {"model": RefreshStatus},
        403: {"model": RefreshError},
        404: {"model": RefreshError},
        503: {"model": RefreshError},
    },
)
def post_refresh(request: Request) -> Any:
    if not refresh_enabled():
        return _error(404, "not_found", "Refresh is not enabled")
    if not same_origin(request):
        return _error(403, "forbidden_origin", "Cross-origin refresh requests are not allowed")
    try:
        # Isolated connection/transaction; SET TRANSACTION must be the first statement.
        with refresh_engine().connect() as conn, conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ WRITE")
            row = (
                conn.execute(text("SELECT * FROM public.public_refresh_request()"))
                .mappings()
                .first()
            )
    except SQLAlchemyError:
        return _error(503, "unavailable", "Refresh temporarily unavailable")
    model = _payload(row, coalesced=bool(row and row["coalesced"]))
    # 202 while a request is (or has just become) in flight; 200 when only reporting a
    # settled request still inside its cooldown.
    return _respond(model, 202 if model.status in _IN_FLIGHT else 200)
