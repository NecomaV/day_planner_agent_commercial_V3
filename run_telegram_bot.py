"""Telegram bot entrypoint.

Loads environment variables from .env automatically (project root).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
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
from app.services.meal_suggest import suggest_meals
from app.services.quick_capture import parse_quick_task
from app.services.reminders import format_reminder_message
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


def _parse_weekday(value: str) -> int | None:
    value = value.strip().lower()
    mapping = {
        "0": 0,
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    return mapping.get(value)


async def _get_user(update: Update, db):
    chat_id = update.effective_chat.id
    return crud.get_or_create_user_by_chat_id(db, chat_id=chat_id)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = _now_local_naive()
    with get_db_session() as db:
        tasks = crud.list_tasks_for_reminders(db, now, settings.REMINDER_LEAD_MIN)
        if not tasks:
            return

        users = {u.id: u for u in crud.list_users(db)}
        grouped: dict[int, list] = defaultdict(list)
        for task in tasks:
            grouped[task.user_id].append(task)

        for user_id, items in grouped.items():
            user = users.get(user_id)
            if not user:
                continue
            try:
                chat_id = int(user.telegram_chat_id)
            except ValueError:
                chat_id = user.telegram_chat_id

            try:
                message = format_reminder_message(items)
                await context.bot.send_message(chat_id=chat_id, text=message)
            except Exception:
                continue

            for task in items:
                task.reminder_sent_at = now

        db.commit()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "Day Planner Agent.\n\n"
        "Commands:\n"
        "/me - show user_id\n"
        "/todo <minutes> <text> - create a backlog task\n"
        "/capture <text> - quick task capture with date/time\n"
        "/call <name> [notes] - log a call and add follow-up\n"
        "/plan [YYYY-MM-DD] - show plan\n"
        "/autoplan <days> [YYYY-MM-DD] - schedule backlog\n"
        "/morning - show today's morning routine\n"
        "/routine_add <offset> <duration> <title> [| kind]\n"
        "/routine_list - list routine steps\n"
        "/routine_del <step_id> - delete routine step\n"
        "/pantry add|remove|list <item>\n"
        "/breakfast - suggest breakfast from pantry\n"
        "/workout today|show|set|clear|list ...\n"
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


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    day = _now_local_naive().date()
    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        routine_tasks = [t for t in tasks if t.task_type == "system" and (t.idempotency_key or "").startswith("routine:")]

        if not routine_tasks:
            await update.message.reply_text("No routine steps yet. Use /routine_add to add one.")
            return

        routine_tasks.sort(key=lambda t: t.planned_start or dt.datetime.max)
        lines = ["Morning routine:"]
        for t in routine_tasks:
            s = t.planned_start.strftime("%H:%M") if t.planned_start else "?"
            e = t.planned_end.strftime("%H:%M") if t.planned_end else "?"
            lines.append(f"- {s}-{e} {t.title} (id={t.id})")

        await update.message.reply_text("\n".join(lines))

async def cmd_routine_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /routine_add <offset_min> <duration_min> <title> [| kind]")
        return

    try:
        offset_min = int(context.args[0])
        duration_min = int(context.args[1])
    except ValueError:
        await update.message.reply_text("offset_min and duration_min must be integers")
        return

    rest = " ".join(context.args[2:]).strip()
    title = rest
    kind = "morning"
    if "|" in rest:
        title, kind = [p.strip() for p in rest.split("|", 1)]

    if not title:
        await update.message.reply_text("Title cannot be empty")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        existing = crud.list_routine_steps(db, user.id, active_only=False)
        position = len(existing) + 1
        step = crud.add_routine_step(
            db,
            user.id,
            title=title,
            offset_min=max(0, offset_min),
            duration_min=max(1, duration_min),
            kind=kind,
            position=position,
        )

    await update.message.reply_text(f"Added routine step: {step.title} (id={step.id})")


async def cmd_routine_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        steps = crud.list_routine_steps(db, user.id, active_only=False)
        if not steps:
            await update.message.reply_text("No routine steps yet. Use /routine_add.")
            return

        lines = ["Routine steps:"]
        for s in steps:
            lines.append(f"- id={s.id} offset={s.offset_min}m dur={s.duration_min}m kind={s.kind} title={s.title}")
        await update.message.reply_text("\n".join(lines))


async def cmd_routine_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /routine_del <step_id>")
        return

    try:
        step_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("step_id must be an integer")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        ok = crud.delete_routine_step(db, user.id, step_id)
        if not ok:
            await update.message.reply_text("Routine step not found")
            return

    await update.message.reply_text(f"Deleted routine step id={step_id}")


async def cmd_pantry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /pantry add|remove|list <item>")
        return

    action = context.args[0].lower()
    rest = " ".join(context.args[1:]).strip()

    with get_db_session() as db:
        user = await _get_user(update, db)

        if action in {"list", "ls"}:
            items = crud.list_pantry_items(db, user.id)
            if not items:
                await update.message.reply_text("Pantry is empty. Add items with /pantry add <item>")
                return
            lines = ["Pantry:"]
            for item in items:
                qty = f" ({item.quantity})" if item.quantity else ""
                lines.append(f"- {item.name}{qty}")
            await update.message.reply_text("\n".join(lines))
            return

        if action == "add":
            if not rest:
                await update.message.reply_text("Usage: /pantry add <item>[=qty]")
                return
            name = rest
            quantity = None
            if "=" in rest:
                name, quantity = [p.strip() for p in rest.split("=", 1)]
            elif ":" in rest:
                name, quantity = [p.strip() for p in rest.split(":", 1)]
            crud.upsert_pantry_item(db, user.id, name=name, quantity=quantity)
            await update.message.reply_text(f"Added to pantry: {name}")
            return

        if action in {"remove", "del", "delete"}:
            if not rest:
                await update.message.reply_text("Usage: /pantry remove <item>")
                return
            ok = crud.remove_pantry_item(db, user.id, name=rest)
            if not ok:
                await update.message.reply_text("Item not found")
                return
            await update.message.reply_text(f"Removed from pantry: {rest}")
            return

    await update.message.reply_text("Usage: /pantry add|remove|list <item>")


async def cmd_breakfast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]

    suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
    if not pantry_names:
        await update.message.reply_text("Pantry is empty. Add items with /pantry add <item>")
        return
    if not suggestions:
        await update.message.reply_text("No matching recipes yet. Add more pantry items.")
        return

    lines = ["Breakfast ideas:"]
    for s in suggestions:
        if s["missing"]:
            missing = ", ".join(s["missing"])
            lines.append(f"- {s['name']} (missing: {missing})")
        else:
            lines.append(f"- {s['name']} (all ingredients available)")
    await update.message.reply_text("\n".join(lines))

async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /workout today|show|set|clear|list ...")
        return

    action = context.args[0].lower()
    args = context.args[1:]

    with get_db_session() as db:
        user = await _get_user(update, db)

        if action == "today":
            weekday = _now_local_naive().weekday()
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text("No workout plan for today.")
                return
            text = plan.details or "(no details)"
            await update.message.reply_text(f"Workout today: {plan.title}\n{text}")
            return

        if action == "show":
            if not args:
                await update.message.reply_text("Usage: /workout show <weekday>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Invalid weekday. Use 0-6 or mon..sun")
                return
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text("No workout plan for that day.")
                return
            text = plan.details or "(no details)"
            await update.message.reply_text(f"Workout plan: {plan.title}\n{text}")
            return

        if action == "set":
            if len(args) < 2:
                await update.message.reply_text("Usage: /workout set <weekday> <title> | <details>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Invalid weekday. Use 0-6 or mon..sun")
                return
            rest = " ".join(args[1:])
            title = rest
            details = None
            if "|" in rest:
                title, details = [p.strip() for p in rest.split("|", 1)]
            plan = crud.set_workout_plan(db, user.id, weekday, title=title, details=details)
            await update.message.reply_text(f"Saved workout plan for weekday {plan.weekday}: {plan.title}")
            return

        if action == "clear":
            if not args:
                await update.message.reply_text("Usage: /workout clear <weekday>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Invalid weekday. Use 0-6 or mon..sun")
                return
            ok = crud.clear_workout_plan(db, user.id, weekday)
            if not ok:
                await update.message.reply_text("Workout plan not found")
                return
            await update.message.reply_text("Workout plan cleared")
            return

        if action == "list":
            plans = crud.list_workout_plans(db, user.id)
            if not plans:
                await update.message.reply_text("No workout plans yet. Use /workout set.")
                return
            lines = ["Workout plans:"]
            for plan in plans:
                lines.append(f"- weekday {plan.weekday}: {plan.title}")
            await update.message.reply_text("\n".join(lines))
            return

    await update.message.reply_text("Usage: /workout today|show|set|clear|list ...")


async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /capture <text>")
        return

    text = " ".join(context.args).strip()
    now = _now_local_naive()
    parsed = parse_quick_task(text, now)

    with get_db_session() as db:
        user = await _get_user(update, db)
        payload = TaskCreate(
            title=parsed.title,
            notes=None,
            estimate_minutes=30,
            planned_start=None,
            planned_end=None,
            due_at=parsed.due_at,
            priority=2,
            kind=None,
            idempotency_key=_idempotency_key(update),
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        if parsed.checklist_items:
            crud.add_checklist_items(db, task.id, parsed.checklist_items)

    when = parsed.due_at.strftime("%Y-%m-%d %H:%M") if parsed.due_at else "(no due time)"
    checklist_info = f" Checklist: {len(parsed.checklist_items)} items." if parsed.checklist_items else ""
    await update.message.reply_text(f"Captured: {task.title} due {when}.{checklist_info}")


async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /call <name> [notes]")
        return

    name = context.args[0].strip()
    notes = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    now = _now_local_naive()
    due_day = now.date() + dt.timedelta(days=max(0, settings.CALL_FOLLOWUP_DAYS))
    due_at = dt.datetime.combine(due_day, dt.time(9, 0))

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.create_task_fields(
            db,
            user.id,
            title=f"Follow up with {name}",
            notes=notes,
            due_at=due_at,
            priority=2,
            estimate_minutes=15,
            task_type="user",
            schedule_source="manual",
            idempotency_key=_idempotency_key(update),
        )
        crud.add_checklist_items(db, task.id, [f"Send summary to {name}"])

    await update.message.reply_text(f"Logged call. Follow-up task created (id={task.id}).")

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
    app.add_handler(CommandHandler("workout", cmd_workout))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("place", cmd_place))
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    app.job_queue.run_repeating(reminder_job, interval=60, first=15)

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
