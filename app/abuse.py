from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

from app.settings import settings


@dataclass
class BlockResult:
    blocked: bool
    retry_after: int


class AuthFailureTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def _cleanup(self, key: str, now: float) -> None:
        window = float(settings.AUTH_FAIL_WINDOW_SEC)
        bucket = self._failures.get(key)
        if not bucket:
            return
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if not bucket:
            self._failures.pop(key, None)

    def is_blocked(self, key: str) -> BlockResult:
        now = time.monotonic()
        with self._lock:
            until = self._blocked_until.get(key)
            if until and now < until:
                return BlockResult(True, max(1, int(until - now)))
            if until and now >= until:
                self._blocked_until.pop(key, None)
            self._cleanup(key, now)
        return BlockResult(False, 0)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._failures.setdefault(key, deque())
            bucket.append(now)
            self._cleanup(key, now)
            if len(bucket) >= settings.AUTH_FAIL_MAX:
                self._blocked_until[key] = now + settings.AUTH_BLOCK_SEC


_tracker: Optional[AuthFailureTracker] = None


def get_auth_failure_tracker() -> AuthFailureTracker:
    global _tracker
    if _tracker is None:
        _tracker = AuthFailureTracker()
    return _tracker


def reset_auth_failure_tracker() -> None:
    global _tracker
    _tracker = None
