from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_active_user as _get_active_user, get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.bot.parsing.values import parse_int_value
from app.bot.utils import now_local_naive
from app.i18n.core import locale_for_user, t


async def handle_location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return
    loc = update.message.location
    now = now_local_naive()
    with get_db_session() as db:
        user = await _get_active_user(update, context, db)
        if not user:
            return
        locale = locale_for_user(user)
        crud.update_user_location(db, user.id, loc.latitude, loc.longitude, now)
        pending = context.user_data.pop("pending_location", None)
        if not pending:
            await update.message.reply_text(t("location.updated", locale=locale))
            return
        task_id = pending.get("task_id")
        radius = pending.get("radius")
        label = pending.get("label")
        task = crud.update_task_location(db, user.id, task_id, loc.latitude, loc.longitude, radius_m=radius, label=label)
        if not task:
            await update.message.reply_text(t("location.task_not_found", locale=locale))
            return
        label_text = f" ({task.location_label})" if task.location_label else ""
        await update.message.reply_text(
            t("location.bound", locale=locale, task_id=task.id, label=label_text)
        )

async def cmd_task_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        if not context.args:
            await update.message.reply_text(t("location.usage", locale=locale))
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("location.id_invalid", locale=locale))
            return
        radius = None
        label_parts = context.args[1:]
        if label_parts and parse_int_value(label_parts[0]) is not None:
            radius = parse_int_value(label_parts[0])
            label_parts = label_parts[1:]
        label = " ".join(label_parts).strip() if label_parts else None
        context.user_data["pending_location"] = {
            "task_id": task_id,
            "radius": radius or 150,
            "label": label,
        }
        await update.message.reply_text(t("location.prompt_send", locale=locale))
