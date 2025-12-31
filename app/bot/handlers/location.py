import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.bot.utils import distance_m, now_local_naive


async def handle_location_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.location:
        return
    loc = update.message.location
    now = now_local_naive()
    with get_db_session() as db:
        user = await _get_active_user(update, context, db)
        if not user:
            return
        crud.update_user_location(db, user.id, loc.latitude, loc.longitude, now)
        pending = context.user_data.pop("pending_location", None)
        if not pending:
            await update.message.reply_text("Локация обновлена.")
            return
        task_id = pending.get("task_id")
        radius = pending.get("radius")
        label = pending.get("label")
        task = crud.update_task_location(db, user.id, task_id, loc.latitude, loc.longitude, radius_m=radius, label=label)
        if not task:
            await update.message.reply_text("Задача не найдена.")
            return
        label_text = f" ({task.location_label})" if task.location_label else ""
        await update.message.reply_text(f"Локация привязана к задаче id={task.id}{label_text}.")

async def cmd_task_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /task_location <id> [радиус_м] [метка]")
        return
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом")
        return
    radius = None
    label_parts = context.args[1:]
    if label_parts and _parse_int_value(label_parts[0]) is not None:
        radius = _parse_int_value(label_parts[0])
        label_parts = label_parts[1:]
    label = " ".join(label_parts).strip() if label_parts else None
    context.user_data["pending_location"] = {
        "task_id": task_id,
        "radius": radius or 150,
        "label": label,
    }
    await update.message.reply_text("Отправьте геолокацию, чтобы привязать ее к задаче.")
