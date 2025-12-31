from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_user
from app.bot.handlers.routine import start_onboarding
from app.bot.rendering.account import cabinet_message, me_message, token_message
from app.bot.rendering.help import start_help_message
from app.settings import settings


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        if not user.is_active:
            user.is_active = True
            db.add(user)
            db.commit()
        if not user.onboarded:
            await update.message.reply_text(
                "Добро пожаловать! Давайте настроим рутину."
            )
            await start_onboarding(update, context)
            return

    await update.message.reply_text(start_help_message())


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        await update.message.reply_text(me_message(user, settings))


async def cmd_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        try:
            token = crud.rotate_user_api_key(db, user.id)
        except Exception:
            await update.message.reply_text("API_KEY_SECRET is not configured on the server.")
            return
        await update.message.reply_text(token_message(token))


async def cmd_cabinet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        steps = crud.list_routine_steps(db, user.id, active_only=False)
        pantry = crud.list_pantry_items(db, user.id)
        workouts = crud.list_workout_plans(db, user.id)
        routine = crud.get_routine(db, user.id)
        await update.message.reply_text(
            cabinet_message(user, routine, steps, pantry, workouts, settings)
        )


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        user.onboarded = False
        db.add(user)
        db.commit()
    context.user_data.pop("onboarding_step", None)
    context.user_data.pop("chat_history", None)
    await update.message.reply_text(
        "Запускаю настройку заново."
    )
    await start_onboarding(update, context)


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        if user.is_active:
            await update.message.reply_text(
                "Аккаунт уже активен."
            )
            return
        user.is_active = True
        db.add(user)
        db.commit()
        await update.message.reply_text("С возвращением.")
        if not user.onboarded:
            await start_onboarding(update, context)


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_user(update, db)
        user.is_active = False
        db.add(user)
        db.commit()
        context.user_data.pop("onboarding_step", None)
        await update.message.reply_text(
            "Вы вышли. Используйте /login, чтобы включить аккаунт."
        )
