"""Engine factory. URL is an argument; env fallback keeps callers simple."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import Engine, create_engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://music_api@localhost:5432/music_dashboard"


def normalize_url(url: str) -> str:
    """Force the psycopg (v3) driver for plain postgres URLs."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def resolve_url(url: str | None = None) -> str:
    return normalize_url(url or os.environ.get("MUSIC_DATABASE_URL") or DEFAULT_DATABASE_URL)


def create_db_engine(url: str | None = None, **kwargs: Any) -> Engine:
    kwargs.setdefault("pool_pre_ping", True)
    return create_engine(resolve_url(url), **kwargs)
