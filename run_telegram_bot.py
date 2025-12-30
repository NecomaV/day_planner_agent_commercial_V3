"""Telegram bot entrypoint.

Loads environment variables from .env automatically (project root).
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app import crud
from app.db import SessionLocal
from app.schemas.tasks import TaskCreate
from app.settings import settings
from app.services.autoplan import autoplan_days, ensure_day_anchors
from app.services.slots import (
    build_busy_intervals,
    day_bounds,
    format_gap_options,
    gaps_from_busy,
    normalize_date_str,
    parse_hhmm,
    task_display_minutes,
)

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("day_planner_bot")


def _now_local_naive() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _idempotency_key(update: Update) -> Optional[str]:
    if not update.message or not update.effective_chat:
        return None
    return f"tg:{update.effective_chat.id}:{update.message.message_id}"


async def _get_user(update: Update, db):
    chat_id = update.effective_chat.id
    return crud.get_or_create_user_by_chat_id(db, chat_id=chat_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "Day Planner Agent.\n\n"
        "Commands:\n"
        "/me - show user_id\n"
        "/todo <minutes> <text> - create a backlog task\n"
        "/plan [YYYY-MM-DD] - show plan\n"
        "/autoplan <days> [YYYY-MM-DD] - schedule backlog\n"
        "/slots <id> [YYYY-MM-DD] - show slots for a task\n"
        "/place <id> <slot#> [HH:MM] - place into a slot\n"
        "/schedule <id> <HH:MM> [YYYY-MM-DD] - schedule by time\n"
        "/unschedule <id> - move back to backlog\n"
        "/done <id> - mark done\n"
        "/delete <id> - delete task\n"
    )
    await update.message.reply_text(msg)


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        api_key_hint = " (X-API-Key required)" if settings.API_KEY else ""
        await update.message.reply_text(
            "User info:\n"
            f"- user_id: {user.id}\n"
            f"- telegram_chat_id: {user.telegram_chat_id}\n"
            f"- timezone: {settings.TZ}\n\n"
            f"API header: X-User-Id = user_id{api_key_hint}"
        )


def _render_day_plan(tasks, backlog, day: dt.date, routine) -> str:
    lines = []
    lines.append(f"Plan for {day.isoformat()}:\n")

    if tasks:
        for i, t in enumerate(tasks, start=1):
            s = t.planned_start.strftime("%H:%M")
            e = t.planned_end.strftime("%H:%M")
            extra = ""
            if t.kind == "workout":
                extra = f" (travel buffer: {routine.workout_travel_oneway_min}m each way)"
            tag = f" [{t.kind}]" if t.kind else ""
            status = "[x]" if t.is_done else "[ ]"
            lines.append(f"{status} {i}) {s}-{e} {t.title}{tag} (id={t.id}){extra}")
    else:
        lines.append("(no scheduled tasks)")

    if backlog:
        lines.append("\nBacklog:")
        for i, t in enumerate(backlog, start=1):
            mins = task_display_minutes(t, routine)
            lines.append(f"[ ] {i}) {t.title} ~ {mins}m (id={t.id})")
        lines.append("\nTip: /autoplan 1")

    return "\n".join(lines)


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_arg = context.args[0] if context.args else None
    if date_arg:
        try:
            day = normalize_date_str(date_arg)
        except ValueError:
            await update.message.reply_text("Date must be YYYY-MM-DD")
            return
    else:
        day = _now_local_naive().date()

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)

        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]

        await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine))


async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /todo <minutes> <text>")
        return

    try:
        estimate = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Minutes must be a number. Example: /todo 30 review inbox")
        return

    title = " ".join(context.args[1:]).strip()
    if not title:
        await update.message.reply_text("Title cannot be empty.")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=None,
            planned_end=None,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=_idempotency_key(update),
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        await update.message.reply_text(f"Created. Task id={task.id} added to backlog.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /done <id>")
        return
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be an integer")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return

        crud.update_task_fields(db, user.id, task_id, is_done=True, schedule_source="manual")
        await update.message.reply_text(f"Done: (id={task_id})")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /delete <id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be an integer")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return

        crud.delete_task(db, user.id, task_id)
        await update.message.reply_text(f"Deleted (id={task_id})")


async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unschedule <id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be an integer")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return
        if task.task_type != "user":
            await update.message.reply_text("Only user tasks can be unscheduled.")
            return

        crud.update_task_fields(db, user.id, task_id, planned_start=None, planned_end=None, schedule_source="manual")
        await update.message.reply_text(f"Moved to backlog (id={task_id}).")


async def cmd_autoplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /autoplan <days> [YYYY-MM-DD]")
        return

    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text("days must be an integer")
        return

    start_date = None
    if len(context.args) >= 2:
        try:
            start_date = normalize_date_str(context.args[1])
        except ValueError:
            await update.message.reply_text("Date must be YYYY-MM-DD")
            return

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        result = autoplan_days(db, user.id, routine, days=days, start_date=start_date)

    suffix = f" {start_date.isoformat()}" if start_date else ""
    await update.message.reply_text(f"Autoplan complete: {result}\nPlan: /plan{suffix}")


def _gaps_for_day(db, user_id: int, day: dt.date, routine):
    ensure_day_anchors(db, user_id, day, routine)

    all_tasks = crud.list_tasks_for_day(db, user_id, day)
    scheduled = [t for t in all_tasks if t.planned_start and not t.is_done]

    now = _now_local_naive()
    day_start, day_end, _morn_s, _morn_e = day_bounds(day, routine, now=now)

    busy = build_busy_intervals(scheduled, routine)
    gaps = gaps_from_busy(busy, day_start, day_end)
    return gaps, day_start, day_end


async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /slots <task_id> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("task_id must be an integer")
        return

    date_arg = context.args[1] if len(context.args) >= 2 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return
        if task.task_type != "user":
            await update.message.reply_text("Only user tasks can be scheduled via /slots.")
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text("Date must be YYYY-MM-DD")
                return
        else:
            day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        text = format_gap_options(task, gaps, routine, day)

    await update.message.reply_text(text)


async def cmd_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /place <task_id> <slot#> [HH:MM]")
        return

    try:
        task_id = int(context.args[0])
        slot_idx = int(context.args[1])
    except ValueError:
        await update.message.reply_text("task_id and slot# must be integers")
        return

    hhmm = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return
        if task.task_type != "user":
            await update.message.reply_text("Only user tasks can be scheduled via /place.")
            return

        day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        if slot_idx < 1 or slot_idx > len(gaps):
            await update.message.reply_text("Invalid slot index. Use /slots <id>.")
            return

        gap = gaps[slot_idx - 1]
        display_minutes = task_display_minutes(task, routine)

        if task.kind == "workout":
            travel = dt.timedelta(minutes=routine.workout_travel_oneway_min)
            core = dt.timedelta(minutes=max(task.estimate_minutes, routine.workout_block_min))
            earliest = gap.start + travel
            latest = gap.end - (core + travel)
        else:
            core = dt.timedelta(minutes=display_minutes)
            earliest = gap.start
            latest = gap.end - core

        if latest < earliest:
            await update.message.reply_text("This slot cannot fit the task. Use /slots again.")
            return

        start = earliest
        if hhmm:
            try:
                t = parse_hhmm(hhmm)
            except Exception:
                await update.message.reply_text("Time must be HH:MM, e.g. 21:30")
                return
            candidate = dt.datetime.combine(day, t)
            if candidate < earliest or candidate > latest:
                await update.message.reply_text(
                    f"Time outside slot. Use {earliest.strftime('%H:%M')}-{latest.strftime('%H:%M')}"
                )
                return
            start = candidate

        end = start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(f"Scheduled (id={task_id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})")


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /schedule <task_id> <HH:MM> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("task_id must be an integer")
        return

    hhmm = context.args[1]
    date_arg = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Task not found")
            return
        if task.task_type != "user":
            await update.message.reply_text("Only user tasks can be scheduled via /schedule.")
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text("Date must be YYYY-MM-DD")
                return
        else:
            day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        try:
            t = parse_hhmm(hhmm)
        except Exception:
            await update.message.reply_text("Time must be HH:MM")
            return

        desired_start = dt.datetime.combine(day, t)
        display_minutes = task_display_minutes(task, routine)

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)

        ok = False
        for gap in gaps:
            if task.kind == "workout":
                travel = dt.timedelta(minutes=routine.workout_travel_oneway_min)
                core = dt.timedelta(minutes=max(task.estimate_minutes, routine.workout_block_min))
                earliest = gap.start + travel
                latest = gap.end - (core + travel)
            else:
                core = dt.timedelta(minutes=display_minutes)
                earliest = gap.start
                latest = gap.end - core

            if earliest <= desired_start <= latest:
                ok = True
                break

        if not ok:
            await update.message.reply_text("Time does not fit available slots. Use /slots <id>.")
            return

        end = desired_start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=desired_start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(f"Scheduled (id={task_id}) {desired_start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})")


def main() -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        # Helpful diagnostics
        hint = (
            "TELEGRAM_BOT_TOKEN is missing.\n"
            f"Looked for .env at: {ENV_PATH}\n"
            f"Current working directory: {Path.cwd()}\n"
            "Fix:\n"
            "1) Ensure file name is exactly '.env' (not .env.txt)\n"
            "2) Ensure it contains: TELEGRAM_BOT_TOKEN=...\n"
            "3) Restart the bot\n"
        )
        raise RuntimeError(hint)

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("autoplan", cmd_autoplan))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("place", cmd_place))
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
