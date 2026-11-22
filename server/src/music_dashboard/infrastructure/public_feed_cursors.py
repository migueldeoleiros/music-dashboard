"""Bounded process-local opaque cursors. Restart/eviction/expiry requires a fresh feed."""

from collections import OrderedDict
from copy import deepcopy
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import HTTPException


class OpaqueCursorStore:
    def __init__(self, ttl_seconds: float = 900, max_entries: int = 4096) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("Cursor bounds must be positive")
        self._ttl = ttl_seconds
        self._capacity = max_entries
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = Lock()

    def issue(self, state: Any) -> str:
        token = token_urlsafe(32)
        with self._lock:
            now = monotonic()
            for key in list(self._entries):
                if self._entries[key][0] <= now:
                    del self._entries[key]
            self._entries[token] = (now + self._ttl, deepcopy(state))
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return token

    def read(self, token: str) -> Any:
        if not isinstance(token, str) or len(token) != 43:
            raise HTTPException(409, "Feed cursor expired; reload feed")
        with self._lock:
            entry = self._entries.get(token)
            if entry is None or entry[0] <= monotonic():
                self._entries.pop(token, None)
                raise HTTPException(409, "Feed cursor expired; reload feed")
            self._entries.move_to_end(token)
            return deepcopy(entry[1])


cursor_store = OpaqueCursorStore()
