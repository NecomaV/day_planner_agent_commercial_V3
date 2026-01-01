import datetime as dt

from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session
from app.bot.utils import distance_m, now_local_naive
from app.i18n.core import locale_for_user, t
from app.bot.rendering.keyboard import yes_no_keyboard
from app.services.reminders import format_reminder_message
from app.settings import settings


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_local_naive()
    with get_db_session() as db:
        users = {u.id: u for u in crud.list_users(db)}

        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            locale = locale_for_user(user)
            tasks = crud.list_tasks_for_reminders(db, user_id, now, settings.REMINDER_LEAD_MIN)
            if not tasks:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id

            try:
                message = format_reminder_message(tasks, locale=locale)
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                continue

            for task in tasks:
                task.reminder_sent_at = now

        # Start prompt at task time
        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            locale = locale_for_user(user)
            start_tasks = crud.list_tasks_for_start_prompt(
                db,
                user_id,
                now,
                settings.START_PROMPT_WINDOW_MIN,
            )
            if not start_tasks:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id
            for task in start_tasks:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=t(
                            "start_prompt.ask",
                            locale=locale,
                            title=task.title,
                            task_id=task.id,
                        ),
                        reply_markup=yes_no_keyboard(locale),
                    )
                    crud.mark_start_prompt_sent(db, user_id, task.id, now)
                except Exception:
                    continue

        # Late prompt
        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            locale = locale_for_user(user)
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
                        text=t(
                            "reminders.late_prompt",
                            locale=locale,
                            title=task.title,
                            task_id=task.id,
                        ),
                    )
                    task.late_prompt_sent_at = now
                except Exception:
                    continue

        # Location-based reminders
        for user_id, user in users.items():
            if not getattr(user, "is_active", True):
                continue
            locale = locale_for_user(user)
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
                        text=t(
                            "reminders.location",
                            locale=locale,
                            label=label,
                            title=task.title,
                            task_id=task.id,
                        ),
                    )
                    task.location_reminder_sent_at = now
                except Exception:
                    continue

        db.commit()
