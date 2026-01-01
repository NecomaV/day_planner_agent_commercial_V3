from __future__ import annotations

import datetime as dt
import time
from collections import deque
from dataclasses import dataclass

from app import crud
from app.settings import settings


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str | None = None
    retry_after: int = 0


class AICircuitBreaker:
    def __init__(self) -> None:
        self._errors: deque[float] = deque()
        self._open_until: float | None = None

    def _cleanup(self, now: float) -> None:
        window = float(settings.AI_ERROR_WINDOW_SEC)
        while self._errors and now - self._errors[0] > window:
            self._errors.popleft()

    def is_open(self) -> GuardResult:
        now = time.monotonic()
        if self._open_until and now < self._open_until:
            return GuardResult(False, "ai.circuit_open", int(self._open_until - now))
        if self._open_until and now >= self._open_until:
            self._open_until = None
        self._cleanup(now)
        return GuardResult(True)

    def record_error(self) -> None:
        now = time.monotonic()
        self._errors.append(now)
        self._cleanup(now)
        if len(self._errors) >= settings.AI_ERROR_THRESHOLD:
            self._open_until = now + settings.AI_COOLDOWN_SEC

    def record_success(self) -> None:
        now = time.monotonic()
        self._cleanup(now)


_breaker = AICircuitBreaker()


def breaker() -> AICircuitBreaker:
    return _breaker


def check_text_limit(text: str) -> GuardResult:
    if len(text) > settings.AI_MAX_TEXT_CHARS:
        return GuardResult(False, "ai.limit.text")
    return GuardResult(True)


def check_audio_limits(duration_sec: int | None, size_bytes: int | None) -> GuardResult:
    if duration_sec is not None and duration_sec > settings.AI_MAX_AUDIO_SECONDS:
        return GuardResult(False, "ai.limit.audio_duration")
    if size_bytes is not None and size_bytes > settings.AI_MAX_AUDIO_BYTES:
        return GuardResult(False, "ai.limit.audio_size")
    return GuardResult(True)


def check_ai_quota(db, user_id: int, *, add_requests: int = 1) -> GuardResult:
    day = dt.date.today()
    counter = crud.get_usage_counter(db, user_id, day)
    used = int(counter.ai_requests) if counter else 0
    if used + add_requests > settings.AI_REQUESTS_PER_DAY:
        return GuardResult(False, "ai.limit.quota")
    return GuardResult(True)


def check_transcribe_quota(db, user_id: int, *, add_seconds: int) -> GuardResult:
    day = dt.date.today()
    counter = crud.get_usage_counter(db, user_id, day)
    used = int(counter.transcribe_seconds) if counter else 0
    limit_seconds = settings.AI_TRANSCRIBE_MINUTES_PER_DAY * 60
    if used + add_seconds > limit_seconds:
        return GuardResult(False, "ai.limit.transcribe_quota")
    return GuardResult(True)


def record_ai_request(db, user_id: int, *, count: int = 1) -> None:
    day = dt.date.today()
    crud.increment_ai_requests(db, user_id, day, amount=count)


def record_transcribe_seconds(db, user_id: int, *, seconds: int) -> None:
    day = dt.date.today()
    crud.increment_transcribe_seconds(db, user_id, day, seconds=seconds)
