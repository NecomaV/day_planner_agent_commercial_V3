from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.bot.middleware.throttle import wrap_throttled
from app.bot.handlers.core import (
    cmd_cabinet,
    cmd_lang,
    cmd_login,
    cmd_logout,
    cmd_me,
    cmd_setup,
    cmd_start,
    cmd_token,
)
from app.bot.handlers.health import cmd_habit, cmd_health, cmd_workout
from app.bot.handlers.location import cmd_task_location, handle_location_message
from app.bot.handlers.messages import handle_text_message, handle_voice_message
from app.bot.handlers.pantry import cmd_breakfast, cmd_pantry
from app.bot.handlers.routine import cmd_morning, cmd_routine_add, cmd_routine_del, cmd_routine_list
from app.bot.handlers.tasks import (
    cmd_autoplan,
    cmd_call,
    cmd_capture,
    cmd_delay,
    cmd_delete,
    cmd_done,
    cmd_place,
    cmd_plan,
    cmd_schedule,
    cmd_slots,
    cmd_todo,
    cmd_unschedule,
)
from app.bot.jobs import reminder_job


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", wrap_throttled(cmd_start)))
    app.add_handler(CommandHandler("me", wrap_throttled(cmd_me)))
    app.add_handler(CommandHandler("token", wrap_throttled(cmd_token)))
    app.add_handler(CommandHandler("todo", wrap_throttled(cmd_todo)))
    app.add_handler(CommandHandler("capture", wrap_throttled(cmd_capture)))
    app.add_handler(CommandHandler("call", wrap_throttled(cmd_call, heavy=True)))
    app.add_handler(CommandHandler("plan", wrap_throttled(cmd_plan, heavy=True)))
    app.add_handler(CommandHandler("autoplan", wrap_throttled(cmd_autoplan, heavy=True)))
    app.add_handler(CommandHandler("morning", wrap_throttled(cmd_morning)))
    app.add_handler(CommandHandler("routine_add", wrap_throttled(cmd_routine_add)))
    app.add_handler(CommandHandler("routine_list", wrap_throttled(cmd_routine_list)))
    app.add_handler(CommandHandler("routine_del", wrap_throttled(cmd_routine_del)))
    app.add_handler(CommandHandler("pantry", wrap_throttled(cmd_pantry)))
    app.add_handler(CommandHandler("breakfast", wrap_throttled(cmd_breakfast)))
    app.add_handler(CommandHandler("health", wrap_throttled(cmd_health)))
    app.add_handler(CommandHandler("habit", wrap_throttled(cmd_habit)))
    app.add_handler(CommandHandler("workout", wrap_throttled(cmd_workout)))
    app.add_handler(CommandHandler("task_location", wrap_throttled(cmd_task_location)))
    app.add_handler(CommandHandler("delay", wrap_throttled(cmd_delay)))
    app.add_handler(CommandHandler("cabinet", wrap_throttled(cmd_cabinet)))
    app.add_handler(CommandHandler("setup", wrap_throttled(cmd_setup)))
    app.add_handler(CommandHandler("login", wrap_throttled(cmd_login)))
    app.add_handler(CommandHandler("logout", wrap_throttled(cmd_logout)))
    app.add_handler(CommandHandler("lang", wrap_throttled(cmd_lang)))
    app.add_handler(CommandHandler("done", wrap_throttled(cmd_done)))
    app.add_handler(CommandHandler("delete", wrap_throttled(cmd_delete)))
    app.add_handler(CommandHandler("unschedule", wrap_throttled(cmd_unschedule)))
    app.add_handler(CommandHandler("slots", wrap_throttled(cmd_slots)))
    app.add_handler(CommandHandler("place", wrap_throttled(cmd_place)))
    app.add_handler(CommandHandler("schedule", wrap_throttled(cmd_schedule)))

    app.add_handler(MessageHandler(filters.VOICE, wrap_throttled(handle_voice_message, heavy=True, dedupe=False)))
    app.add_handler(MessageHandler(filters.LOCATION, wrap_throttled(handle_location_message, dedupe=False)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrap_throttled(handle_text_message)))

    app.job_queue.run_repeating(reminder_job, interval=60, first=15)
