from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.db import SessionLocal


@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class UserContext:
    user: object
    routine: object
    now: dt.datetime


async def get_user(update: Update, db):
    chat_id = update.effective_chat.id
    return crud.get_or_create_user_by_chat_id(db, chat_id=chat_id)


async def get_active_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db):
    user = await get_user(update, db)
    if not user.is_active:
        await update.message.reply_text(
            "Вы вышли. Используйте /login для активации."
        )
        return None
    return user


async def get_ready_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    *,
    start_onboarding=None,
):
    user = await get_active_user(update, context, db)
    if not user:
        return None
    if not user.onboarded:
        await update.message.reply_text("Сначала пройдем настройку.")
        if start_onboarding is not None:
            await start_onboarding(update, context)
        return None
    return user


def build_user_context(user, routine) -> UserContext:
    return UserContext(user=user, routine=routine, now=dt.datetime.now().replace(microsecond=0))
