import asyncio

from app.bot.throttle import BotThrottle
from app.settings import settings


def test_dedupe_blocks_repeated_text(monkeypatch):
    monkeypatch.setattr(settings, "BOT_DEDUPE_WINDOW_SEC", 10)
    throttle = BotThrottle()

    first = throttle.check("u1", text="привет", heavy=False)
    second = throttle.check("u1", text="привет", heavy=False)

    assert first.allowed
    assert not second.allowed
    assert second.deduped
    assert second.reason == "bot.throttle.dedupe"


def test_burst_limit_blocks(monkeypatch):
    monkeypatch.setattr(settings, "BOT_BURST_MAX", 2)
    monkeypatch.setattr(settings, "BOT_BURST_WINDOW_SEC", 60)
    monkeypatch.setattr(settings, "BOT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(settings, "BOT_HEAVY_COOLDOWN_SEC", 0)
    throttle = BotThrottle()

    assert throttle.check("u1").allowed
    assert throttle.check("u1").allowed
    denied = throttle.check("u1")

    assert not denied.allowed
    assert denied.reason == "bot.throttle.burst"


def test_cooldown_blocks(monkeypatch):
    monkeypatch.setattr(settings, "BOT_COOLDOWN_SEC", 60)
    monkeypatch.setattr(settings, "BOT_BURST_MAX", 10)
    throttle = BotThrottle()

    assert throttle.check("u1").allowed
    denied = throttle.check("u1")

    assert not denied.allowed
    assert denied.reason == "bot.throttle.cooldown"


def test_heavy_lock_blocks(monkeypatch):
    monkeypatch.setattr(settings, "BOT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(settings, "BOT_HEAVY_COOLDOWN_SEC", 0)
    monkeypatch.setattr(settings, "BOT_BURST_MAX", 10)
    throttle = BotThrottle()

    lock = throttle.get_lock("u1")

    async def _acquire():
        await lock.acquire()

    asyncio.run(_acquire())
    denied = throttle.check("u1", heavy=True)
    lock.release()

    assert not denied.allowed
    assert denied.reason == "bot.throttle.busy"
