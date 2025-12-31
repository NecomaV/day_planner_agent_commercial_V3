import datetime as dt

from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session
from app.bot.utils import distance_m, now_local_naive
from app.services.reminders import format_reminder_message
from app.settings import settings


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_local_naive()
    with get_db_session() as db:
        users = {u.id: u for u in crud.list_users(db)}

        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            tasks = crud.list_tasks_for_reminders(db, user_id, now, settings.REMINDER_LEAD_MIN)
            if not tasks:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id

            try:
                message = format_reminder_message(tasks)
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                continue

            for task in tasks:
                task.reminder_sent_at = now

        # Late prompt
        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            late_tasks = crud.list_late_tasks(db, user_id, now, settings.DELAY_GRACE_MIN)
            if not late_tasks:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id
            for task in late_tasks:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"You are running late for task: {task.title} (id={task.id}). "
                            "Use /delay <id> <minutes>."
                        ),
                    )
                    task.late_prompt_sent_at = now
                except Exception:
                    continue

        # Location-based reminders
        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            if user.last_lat is None or user.last_lon is None or user.last_location_at is None:
                continue
            if (now - user.last_location_at) > dt.timedelta(minutes=settings.LOCATION_STALE_MIN):
                continue
            tasks_with_location = crud.list_tasks_with_location(db, user_id)
            if not tasks_with_location:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id
            for task in tasks_with_location:
                if task.location_reminder_sent_at is not None:
                    continue
                radius = task.location_radius_m or 150
                dist = distance_m(user.last_lat, user.last_lon, task.location_lat, task.location_lon)
                if dist > radius:
                    continue
                label = f" ({task.location_label})" if task.location_label else ""
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "\u0412\u044b \u0440\u044f\u0434\u043e\u043c \u0441 \u043b\u043e\u043a\u0430\u0446\u0438\u0435\u0439"
                            f"{label}. \u0417\u0430\u0434\u0430\u0447\u0430: {task.title} (id={task.id})"
                        ),
                    )
                    task.location_reminder_sent_at = now
                except Exception:
                    continue

        db.commit()
