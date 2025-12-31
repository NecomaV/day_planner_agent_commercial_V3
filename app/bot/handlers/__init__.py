from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.bot.handlers.core import (
    cmd_cabinet,
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
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("token", cmd_token))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("capture", cmd_capture))
    app.add_handler(CommandHandler("call", cmd_call))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("autoplan", cmd_autoplan))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("routine_add", cmd_routine_add))
    app.add_handler(CommandHandler("routine_list", cmd_routine_list))
    app.add_handler(CommandHandler("routine_del", cmd_routine_del))
    app.add_handler(CommandHandler("pantry", cmd_pantry))
    app.add_handler(CommandHandler("breakfast", cmd_breakfast))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("habit", cmd_habit))
    app.add_handler(CommandHandler("workout", cmd_workout))
    app.add_handler(CommandHandler("task_location", cmd_task_location))
    app.add_handler(CommandHandler("delay", cmd_delay))
    app.add_handler(CommandHandler("cabinet", cmd_cabinet))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("place", cmd_place))
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    app.job_queue.run_repeating(reminder_job, interval=60, first=15)
