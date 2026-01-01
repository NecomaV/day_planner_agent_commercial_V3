from __future__ import annotations

from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.context import get_db_session, get_user
from app.bot.throttle import throttle
from app.i18n.core import locale_for_user, t

HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


async def _resolve_locale(update: Update) -> str:
    if not update.effective_chat:
        return "ru"
    with get_db_session() as db:
        user = await get_user(update, db)
        return locale_for_user(user)


def wrap_throttled(handler: HandlerFunc, *, heavy: bool = False, dedupe: bool = True) -> HandlerFunc:
    async def _wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            await handler(update, context)
            return

        text = update.message.text.strip() if dedupe and update.message.text else None
        user_key = str(getattr(update.effective_user, "id", None) or update.effective_chat.id)

        decision = throttle().check(user_key, text=text, heavy=heavy)
        if not decision.allowed:
            if decision.deduped:
                return
            locale = await _resolve_locale(update)
            reason_key = decision.reason or "bot.throttle.cooldown"
            await update.message.reply_text(
                t(reason_key, locale=locale, retry_after=decision.retry_after),
            )
            return

        if heavy:
            lock = throttle().get_lock(user_key)
            if lock.locked():
                locale = await _resolve_locale(update)
                await update.message.reply_text(t("bot.throttle.busy", locale=locale))
                return
            async with lock:
                await handler(update, context)
            return

        await handler(update, context)

    return _wrapped
