from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

from app.settings import settings

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    redis = None


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_sec: int) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and now - bucket[0] > window_sec:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_sec - (now - bucket[0])))
                return RateLimitResult(False, retry_after)
            bucket.append(now)
        return RateLimitResult(True, 0)


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        if redis is None:
            raise RuntimeError("redis is not available")
        self._client = redis.Redis.from_url(url, decode_responses=True)

    def allow(self, key: str, limit: int, window_sec: int) -> RateLimitResult:
        now = int(time.time())
        window = now // window_sec
        redis_key = f"rl:{key}:{window}"
        count = int(self._client.incr(redis_key))
        if count == 1:
            self._client.expire(redis_key, window_sec + 1)
        if count > limit:
            retry_after = max(1, window_sec - (now % window_sec))
            return RateLimitResult(False, retry_after)
        return RateLimitResult(True, 0)


_rate_limiter: Optional[object] = None


def get_rate_limiter():
    global _rate_limiter
    if _rate_limiter is not None:
        return _rate_limiter
    if settings.REDIS_URL:
        try:
            _rate_limiter = RedisRateLimiter(settings.REDIS_URL)
            return _rate_limiter
        except Exception:
            _rate_limiter = InMemoryRateLimiter()
            return _rate_limiter
    _rate_limiter = InMemoryRateLimiter()
    return _rate_limiter


def reset_rate_limiter() -> None:
    global _rate_limiter
    _rate_limiter = None
