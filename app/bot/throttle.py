from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from app.settings import settings


@dataclass
class ThrottleDecision:
    allowed: bool
    deduped: bool = False
    retry_after: int = 0
    reason: str | None = None


@dataclass
class _UserState:
    last_at: float = 0.0
    last_heavy_at: float = 0.0
    burst: deque[float] = field(default_factory=deque)
    last_text: str | None = None
    last_text_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BotThrottle:
    def __init__(self) -> None:
        self._states: dict[str, _UserState] = {}

    def _state(self, user_id: str) -> _UserState:
        if user_id not in self._states:
            self._states[user_id] = _UserState()
        return self._states[user_id]

    def check(self, user_id: str, *, text: str | None = None, heavy: bool = False) -> ThrottleDecision:
        state = self._state(user_id)
        now = time.monotonic()

        if text:
            if state.last_text == text and (now - state.last_text_at) < settings.BOT_DEDUPE_WINDOW_SEC:
                return ThrottleDecision(False, deduped=True, reason="bot.throttle.dedupe")
            state.last_text = text
            state.last_text_at = now

        while state.burst and now - state.burst[0] > settings.BOT_BURST_WINDOW_SEC:
            state.burst.popleft()

        if len(state.burst) >= settings.BOT_BURST_MAX:
            retry = max(1, int(settings.BOT_BURST_WINDOW_SEC - (now - state.burst[0])))
            return ThrottleDecision(False, retry_after=retry, reason="bot.throttle.burst")

        if now - state.last_at < settings.BOT_COOLDOWN_SEC:
            retry = max(1, int(settings.BOT_COOLDOWN_SEC - (now - state.last_at)))
            return ThrottleDecision(False, retry_after=retry, reason="bot.throttle.cooldown")

        if heavy and now - state.last_heavy_at < settings.BOT_HEAVY_COOLDOWN_SEC:
            retry = max(1, int(settings.BOT_HEAVY_COOLDOWN_SEC - (now - state.last_heavy_at)))
            return ThrottleDecision(False, retry_after=retry, reason="bot.throttle.heavy")

        if heavy and state.lock.locked():
            return ThrottleDecision(False, reason="bot.throttle.busy")

        state.burst.append(now)
        state.last_at = now
        if heavy:
            state.last_heavy_at = now
        return ThrottleDecision(True)

    def get_lock(self, user_id: str) -> asyncio.Lock:
        return self._state(user_id).lock


_throttle = BotThrottle()


def throttle() -> BotThrottle:
    return _throttle
