# -*- coding: utf-8 -*-
"""run_telegram_bot.py

Telegram bot entrypoint.

Loads environment variables from .env automatically (project root).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Optional
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv  # <-- IMPORTANT

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app import crud
from app.db import SessionLocal
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

# ----------------------------
# .env loading (robust)
# ----------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)
# Fallback: also try current working directory (sometimes user launches from another folder)
load_dotenv(override=False)


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


async def _get_user(update: Update, db):
    chat_id = update.effective_chat.id
    return crud.get_or_create_user_by_chat_id(db, chat_id=chat_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "Day Planner Agent готов.\n\n"
        "Команды:\n"
        "/me — показать ваш user_id\n"
        "/todo <минуты> <текст> — создать задачу без времени\n"
        "/plan [YYYY-MM-DD] — показать план\n"
        "/autoplan <дней> [YYYY-MM-DD] — разложить бэклог\n"
        "/slots <id> [YYYY-MM-DD] — показать окна для задачи\n"
        "/place <id> <окно#> [HH:MM] — поставить задачу\n"
        "/schedule <id> <HH:MM> [YYYY-MM-DD] — поставить по точному времени\n"
        "/unschedule <id> — убрать время (в бэклог)\n"
        "/done <id> — отметить выполненной\n"
        "/delete <id> — удалить\n"
    )
    await update.message.reply_text(msg)


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        await update.message.reply_text(
            "Ваш профиль:\n"
            f"- user_id: {user.id}\n"
            f"- telegram_chat_id: {user.telegram_chat_id}\n"
            f"- timezone: {settings.DEFAULT_TIMEZONE}\n\n"
            "Для Swagger/API используйте заголовок: X-User-Id = ваш user_id"
        )


def _render_day_plan(tasks, backlog, day: dt.date, routine) -> str:
    lines = []
    lines.append(f"План на сегодня ({day.isoformat()}):\n")

    if tasks:
        for i, t in enumerate(tasks, start=1):
            s = t.planned_start.strftime("%H:%M")
            e = t.planned_end.strftime("%H:%M")
            extra = ""
            if t.kind == "workout":
                extra = f"  (дорога: {routine.workout_travel_oneway_min}м в одну сторону)"
            icon = ""
            if t.kind == "meal":
                icon = "🍽 "
            elif t.kind == "morning":
                icon = "🧼 "

            status = "✅" if t.is_done else "⏳"
            lines.append(
                f"{status} {i}) {s}-{e} {icon}{t.title} (id={t.id}){extra}"
            )
    else:
        lines.append("(пока пусто)")

    if backlog:
        lines.append("\nБэклог (без времени):")
        for i, t in enumerate(backlog, start=1):
            mins = task_display_minutes(t, routine)
            lines.append(f"⏳ {i}) {t.title} — {mins}м (id={t.id})")
        lines.append("\nЧтобы разложить бэклог по времени: /autoplan 1")

    return "\n".join(lines)


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_arg = context.args[0] if context.args else None
    day = normalize_date_str(date_arg) if date_arg else _now_local_naive().date()

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
        await update.message.reply_text("Формат: /todo <минуты> <текст>")
        return

    try:
        estimate = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Минуты должны быть числом. Пример: /todo 30 позвонить")
        return

    title = " ".join(context.args[1:]).strip()
    if not title:
        await update.message.reply_text("Укажите текст задачи.")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.create_task(
            db,
            user_id=user.id,
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=None,
            planned_end=None,
            due_at=None,
            priority=2,
            kind=None,
            task_type="user",
            anchor_key=None,
            request_id=None,
            schedule_source="manual",
        )
        await update.message.reply_text(f"Ок. Создал задачу (id={task.id}) в бэклог.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /done <id>")
        return
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return

        crud.update_task(db, user.id, task_id, is_done=True, schedule_source="manual")
        await update.message.reply_text(f"✅ Готово: (id={task_id})")


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /delete <id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return

        crud.delete_task(db, user.id, task_id)
        await update.message.reply_text(f"🗑 Удалил (id={task_id})")


async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /unschedule <id>")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_user(update, db)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Нельзя убирать время у якорей/системных задач.")
            return

        crud.update_task(db, user.id, task_id, planned_start=None, planned_end=None, schedule_source="manual")
        await update.message.reply_text(f"Ок. Перенёс в бэклог (id={task_id}).")


async def cmd_autoplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /autoplan <дней> [YYYY-MM-DD]")
        return

    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text("дней должно быть числом")
        return

    start_date = None
    if len(context.args) >= 2:
        start_date = normalize_date_str(context.args[1])

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        result = autoplan_days(db, user.id, routine, days=days, start_date=start_date)

    await update.message.reply_text(
        f"Готово. Autoplan: {result}\nПроверь план: /plan" + (f" {start_date.isoformat()}" if start_date else "")
    )


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
        await update.message.reply_text("Формат: /slots <task_id> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("task_id должен быть числом")
        return

    date_arg = context.args[1] if len(context.args) >= 2 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Это якорь/системная задача. Для неё окна не нужны.")
            return

        day = normalize_date_str(date_arg) if date_arg else (task.planned_start.date() if task.planned_start else _now_local_naive().date())

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        text = format_gap_options(task, gaps, routine, day)

    await update.message.reply_text(text)


async def cmd_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /place <task_id> <slot#> [HH:MM]")
        return

    try:
        task_id = int(context.args[0])
        slot_idx = int(context.args[1])
    except ValueError:
        await update.message.reply_text("task_id и slot# должны быть числами")
        return

    hhmm = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Нельзя переносить якоря/системные задачи.")
            return

        day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        if slot_idx < 1 or slot_idx > len(gaps):
            await update.message.reply_text("Неверный номер окна. Посмотри: /slots <id>")
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
            await update.message.reply_text("В это окно задача не помещается. Посмотри другие окна: /slots")
            return

        start = earliest
        if hhmm:
            try:
                t = parse_hhmm(hhmm)
            except Exception:
                await update.message.reply_text("Время должно быть в формате HH:MM, например 21:30")
                return
            candidate = dt.datetime.combine(day, t)
            if candidate < earliest or candidate > latest:
                await update.message.reply_text(
                    f"Это время не подходит. Допустимый диапазон старта в этом окне: {earliest.strftime('%H:%M')}–{latest.strftime('%H:%M')}"
                )
                return
            start = candidate

        end = start + core
        crud.update_task(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(f"Ок. Поставил: (id={task_id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})")


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Формат: /schedule <task_id> <HH:MM> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("task_id должен быть числом")
        return

    hhmm = context.args[1]
    date_arg = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_user(update, db)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Нельзя переносить якоря/системные задачи.")
            return

        day = normalize_date_str(date_arg) if date_arg else (task.planned_start.date() if task.planned_start else _now_local_naive().date())

        try:
            t = parse_hhmm(hhmm)
        except Exception:
            await update.message.reply_text("Время должно быть HH:MM")
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
            await update.message.reply_text("В это время поставить нельзя (пересечение или вне окон). Посмотри /slots <id>.")
            return

        end = desired_start + core
        crud.update_task(db, user.id, task_id, planned_start=desired_start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(f"Ок. Поставил: (id={task_id}) {desired_start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
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
