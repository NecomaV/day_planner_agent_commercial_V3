"""Telegram bot entrypoint.

Loads environment variables from .env automatically (project root).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app import crud
from app.db import SessionLocal
from app.schemas.tasks import TaskCreate
from app.settings import settings
from app.services.autoplan import autoplan_days, ensure_day_anchors
from app.services.ai_chat import chat_reply
from app.services.ai_transcribe import transcribe_audio
from app.services.ai_intent import parse_intent, suggest_routine_steps
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


def _parse_command_text(text: str) -> tuple[str, list[str]] | None:
    raw = text.strip()
    if not raw.startswith("/"):
        return None
    parts = raw.lstrip("/").split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def _extract_task_ids(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\b\d+\b", text)]


def _looks_like_greeting(text: str) -> bool:
    lower = re.sub(r"[^\w\s]", "", text.strip().lower())
    return lower in {
        "привет",
        "приветик",
        "здравствуй",
        "здравствуйте",
        "добрый день",
        "доброе утро",
        "добрый вечер",
        "hi",
        "hello",
        "hey",
    }


def _should_create_task(text: str) -> bool:
    lower = text.lower()
    triggers = [
        "создай задачу",
        "добавь задачу",
        "добавить задачу",
        "поставь задачу",
        "задача:",
        "сделать:",
        "нужно сделать",
        "надо сделать",
        "напомни",
        "напомнить",
        "todo",
        "task",
    ]
    return any(t in lower for t in triggers)


def _get_chat_history(context: ContextTypes.DEFAULT_TYPE, limit: int = 8) -> list[dict[str, str]]:
    history = context.user_data.get("chat_history") or []
    return history[-limit:]


def _append_chat_history(context: ContextTypes.DEFAULT_TYPE, role: str, content: str, limit: int = 8) -> None:
    history = context.user_data.get("chat_history") or []
    history.append({"role": role, "content": content})
    context.user_data["chat_history"] = history[-limit:]


def _build_assistant_context(db, user) -> str:
    now = _now_local_naive()
    routine = crud.get_routine(db, user.id)
    day = now.date()
    tasks = crud.list_tasks_for_day(db, user.id, day)
    scheduled = [t for t in tasks if t.planned_start and not t.is_done]
    backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
    scheduled.sort(key=lambda t: t.planned_start)

    lines = [
        f"Дата и время: {now.strftime('%Y-%m-%d %H:%M')} ({settings.TZ})",
        f"Цели сна: подъем {routine.sleep_target_wakeup}, сон {routine.sleep_target_bedtime}",
        f"Запланировано на сегодня: {len(scheduled)}",
    ]
    if scheduled:
        lines.append("Ближайшие задачи:")
        for t in scheduled[:5]:
            when = t.planned_start.strftime("%H:%M")
            lines.append(f"- {when} {t.title} (id={t.id})")
    lines.append(f"Бэклог: {len(backlog)}")
    return "\n".join(lines)


def _assistant_system_prompt() -> str:
    return (
        "Ты — русскоязычный ИИ‑ассистент для бизнеса (в стиле Джарвис), "
        "помогаешь планировать день, отвечаешь на вопросы, предлагаешь идеи и уточнения. "
        "Будь кратким, дружелюбным и практичным. "
        "Если для ответа нужна дополнительная информация, задай уточняющий вопрос. "
        "Не создавай задачи сам — предложи пользователю сказать: «создай задачу ...», "
        "если это уместно."
    )


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
        "пн": 0,
        "понедельник": 0,
        "вт": 1,
        "вторник": 1,
        "ср": 2,
        "среда": 2,
        "чт": 3,
        "четверг": 3,
        "пт": 4,
        "пятница": 4,
        "сб": 5,
        "суббота": 5,
        "вс": 6,
        "воскресенье": 6,
    }
    return mapping.get(value)


def _parse_time_value(text: str) -> dt.time | None:
    lower = text.lower()
    range_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?", lower)
    meridian_pm = bool(re.search(r"\b(pm|вечера|дня)\b", lower))
    meridian_am = bool(re.search(r"\b(am|утра|ночи)\b", lower))

    def apply_meridian(hh: int) -> int:
        if meridian_pm and hh < 12:
            return hh + 12
        if meridian_am and hh == 12:
            return 0
        return hh

    if range_match:
        h1 = int(range_match.group(1))
        m1 = int(range_match.group(2) or 0)
        h2 = int(range_match.group(3))
        m2 = int(range_match.group(4) or 0)
        h1 = apply_meridian(h1)
        h2 = apply_meridian(h2)
        if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
            return None
        start = dt.datetime.combine(dt.date.today(), dt.time(h1, m1))
        end = dt.datetime.combine(dt.date.today(), dt.time(h2, m2))
        if end <= start:
            return dt.time(h1, m1)
        midpoint = start + (end - start) / 2
        return midpoint.time().replace(second=0, microsecond=0)

    m = re.search(r"(\d{1,2})(?::(\d{2}))?", lower)
    if not m:
        return None
    hh = apply_meridian(int(m.group(1)))
    mm = int(m.group(2) or 0)
    if hh > 23 or mm > 59:
        return None
    return dt.time(hh, mm)


async def _get_user(update: Update, db):
    chat_id = update.effective_chat.id
    return crud.get_or_create_user_by_chat_id(db, chat_id=chat_id)


async def _get_active_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db):
    user = await _get_user(update, db)
    if not user.is_active:
        await update.message.reply_text("Вы вышли. Используйте /login для активации.")
        return None
    return user


async def _get_ready_user(update: Update, context: ContextTypes.DEFAULT_TYPE, db):
    user = await _get_active_user(update, context, db)
    if not user:
        return None
    if not user.onboarded:
        await update.message.reply_text("Сначала пройдем настройку.")
        await _start_onboarding(update, context)
        return None
    return user


def _split_items(text: str) -> list[str]:
    items = [i.strip() for i in re.split(r"[;,]", text) if i.strip()]
    if len(items) == 1 and re.search(r"\band\b", items[0], re.IGNORECASE):
        items = [i.strip() for i in re.split(r"\band\b", items[0], flags=re.IGNORECASE) if i.strip()]
    return items


def _extract_routine_items(text: str) -> list[str]:
    lower = text.lower()
    triggers = [
        "every morning",
        "each morning",
        "add to routine",
        "morning routine",
        "routine:",
        "каждое утро",
        "утренняя рутина",
        "добавь в рутину",
        "в рутину",
        "рутина:",
    ]
    if not any(t in lower for t in triggers):
        return []
    cleaned = text
    for t in triggers:
        cleaned = re.sub(re.escape(t), "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(":", " ")
    return _split_items(cleaned)


DATE_TOKEN_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
RUS_WEEKDAY_MAP = {
    "пн": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "ср": 2,
    "среда": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "сб": 5,
    "суббота": 5,
    "вс": 6,
    "воскресенье": 6,
}


def _detect_day_from_text(text: str, now: dt.datetime) -> dt.date:
    lower = text.lower()
    if "сегодня" in lower or "today" in lower:
        return now.date()
    if "послезавтра" in lower:
        return now.date() + dt.timedelta(days=2)
    if "завтра" in lower or "tomorrow" in lower:
        return now.date() + dt.timedelta(days=1)
    m = DATE_TOKEN_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = re.search(r"\b(следующ(?:ий|ая|ее)\s+)?(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b", lower)
    if m:
        token = m.group(2)
        target = RUS_WEEKDAY_MAP.get(token, now.weekday())
        days_ahead = (target - now.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return now.date() + dt.timedelta(days=days_ahead)
    return now.date()


def _is_plan_request(text: str) -> bool:
    return bool(re.search(r"\b(план|расписание|график|розклад|plan)\b", text, flags=re.IGNORECASE))


def _is_breakfast_request(text: str) -> bool:
    return bool(re.search(r"\b(завтрак|breakfast)\b", text, flags=re.IGNORECASE))


def _is_autoplan_request(text: str) -> bool:
    return bool(re.search(r"\b(автоплан|autoplan|распланируй|распланировать)\b", text, flags=re.IGNORECASE))


def _parse_autoplan_args(text: str) -> list[str]:
    days = None
    m = re.search(r"\b(\d{1,2})\b", text)
    if m:
        days = m.group(1)
    date_match = DATE_TOKEN_RE.search(text)
    args = []
    args.append(days or "1")
    if date_match:
        args.append(date_match.group(1))
    return args


def _format_task_choice(task, routine) -> str:
    if task.planned_start:
        when = task.planned_start.strftime("%H:%M")
    elif task.due_at:
        when = task.due_at.strftime("%Y-%m-%d %H:%M")
    else:
        when = "без времени"
    mins = task_display_minutes(task, routine)
    return f"- id={task.id} {task.title} ({when}, ~{mins}м)"


def _list_open_tasks(db, user, routine, limit: int = 8) -> list:
    today = _now_local_naive().date()
    tasks = crud.list_tasks_for_day(db, user.id, today)
    open_tasks = [t for t in tasks if not t.is_done and t.task_type == "user"]
    open_tasks.sort(key=lambda t: (t.planned_start is None, t.planned_start or dt.datetime.max, t.id))
    return open_tasks[:limit]


async def _prompt_task_selection(action: str, update: Update, context: ContextTypes.DEFAULT_TYPE, db, user, routine) -> None:
    tasks = _list_open_tasks(db, user, routine)
    if not tasks:
        await update.message.reply_text("Нет активных задач для выбора.")
        return
    context.user_data["pending_action"] = {
        "action": action,
        "candidate_ids": [t.id for t in tasks],
    }
    lines = ["Уточните id задачи:"]
    lines.extend([_format_task_choice(t, routine) for t in tasks])
    lines.append("Можно ответить только числом, например: 12. Для отмены: отмена.")
    await update.message.reply_text("\n".join(lines))


async def _apply_task_actions(action: str, task_ids: list[int], update: Update, db, user) -> bool:
    if not task_ids:
        return False
    task_ids = list(dict.fromkeys(task_ids))
    deleted: list[int] = []
    done: list[int] = []
    unscheduled: list[int] = []
    skipped: list[int] = []

    for task_id in task_ids:
        task = crud.get_task(db, user.id, task_id)
        if not task:
            skipped.append(task_id)
            continue
        if action == "delete":
            crud.delete_task(db, user.id, task_id)
            deleted.append(task_id)
            continue
        if action == "done":
            crud.update_task_fields(db, user.id, task_id, is_done=True, schedule_source="manual")
            done.append(task_id)
            continue
        if action == "unschedule":
            if task.task_type != "user":
                skipped.append(task_id)
                continue
            crud.update_task_fields(db, user.id, task_id, planned_start=None, planned_end=None, schedule_source="manual")
            unscheduled.append(task_id)
            continue

    parts = []
    if deleted:
        parts.append(f"Удалено: {', '.join(str(i) for i in deleted)}")
    if done:
        parts.append(f"Готово: {', '.join(str(i) for i in done)}")
    if unscheduled:
        parts.append(f"Перемещено в бэклог: {', '.join(str(i) for i in unscheduled)}")
    if skipped:
        parts.append(f"Пропущено: {', '.join(str(i) for i in skipped)}")

    if not parts:
        await update.message.reply_text("Задачи не найдены.")
        return False
    await update.message.reply_text("\n".join(parts))
    return True


async def _handle_pending_action(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    routine,
) -> bool:
    pending = context.user_data.get("pending_action")
    if not pending:
        return False
    answer = text.strip().lower()
    if answer in {"отмена", "cancel", "stop", "стоп"}:
        context.user_data.pop("pending_action", None)
        await update.message.reply_text("Ок, отменено.")
        return True
    ids = _extract_task_ids(text)
    if not ids:
        await update.message.reply_text("Пришлите id задачи или напишите «отмена».")
        return True
    candidate_ids = set(pending.get("candidate_ids") or [])
    if candidate_ids and any(task_id not in candidate_ids for task_id in ids):
        await update.message.reply_text("Пожалуйста, выберите id из списка.")
        return True
    context.user_data.pop("pending_action", None)
    return await _apply_task_actions(pending.get("action", ""), ids, update, db, user)


async def _run_command_by_name(
    name: str,
    args: list[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    handlers = {
        "start": cmd_start,
        "me": cmd_me,
        "todo": cmd_todo,
        "capture": cmd_capture,
        "call": cmd_call,
        "plan": cmd_plan,
        "autoplan": cmd_autoplan,
        "morning": cmd_morning,
        "routine_add": cmd_routine_add,
        "routine_list": cmd_routine_list,
        "routine_del": cmd_routine_del,
        "pantry": cmd_pantry,
        "breakfast": cmd_breakfast,
        "workout": cmd_workout,
        "cabinet": cmd_cabinet,
        "login": cmd_login,
        "logout": cmd_logout,
        "done": cmd_done,
        "delete": cmd_delete,
        "unschedule": cmd_unschedule,
        "slots": cmd_slots,
        "place": cmd_place,
        "schedule": cmd_schedule,
    }
    handler = handlers.get(name)
    if not handler:
        return False
    original_args = context.args
    context.args = args
    try:
        await handler(update, context)
    finally:
        context.args = original_args
    return True

async def _start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["onboarding_step"] = "wake"
    await update.message.reply_text("Добро пожаловать! Во сколько обычно просыпаетесь? (HH:MM). Можно написать «пропустить».")


def _apply_wake_time(routine, wake_str: str) -> None:
    routine.sleep_target_wakeup = wake_str
    try:
        t = parse_hhmm(wake_str)
    except Exception:
        return
    base = dt.datetime.combine(dt.date.today(), t)
    b_start = (base + dt.timedelta(minutes=30)).time().strftime("%H:%M")
    b_end = (base + dt.timedelta(hours=3)).time().strftime("%H:%M")
    routine.breakfast_window_start = b_start
    routine.breakfast_window_end = b_end


def _suggest_routine_steps(goal: str) -> list[str]:
    goal = goal.strip().lower()
    if any(x in goal for x in ["fitness", "gym", "workout", "health"]):
        return ["Стакан воды", "Растяжка", "Легкая тренировка", "Белковый завтрак", "План дня"]
    if any(x in goal for x in ["work", "focus", "business", "study", "learn"]):
        return ["Стакан воды", "Приоритеты дня", "Фокус-блок", "Завтрак", "Разбор входящих"]
    if any(x in goal for x in ["family", "kids", "home"]):
        return ["Стакан воды", "Проверка семейного расписания", "Завтрак", "Сборы", "Быстрая уборка"]
    return ["Стакан воды", "Растяжка", "Завтрак", "План дня"]


async def _handle_onboarding_text(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> bool:
    step = context.user_data.get("onboarding_step")
    if not step and user.onboarded:
        return False

    if not step:
        await _start_onboarding(update, context)
        return True

    routine = crud.get_routine(db, user.id)

    if step == "wake":
        if text.strip().lower() in {"skip", "later", "пропустить", "потом", "не знаю"}:
            context.user_data["onboarding_step"] = "goal"
            await update.message.reply_text("Какой сейчас главный фокус? (работа, фитнес, семья, учеба, другое)")
            return True
        t = _parse_time_value(text) or None
        if not t:
            await update.message.reply_text("Введите время в формате HH:MM, например 07:30")
            return True
        wake_str = f"{t.hour:02d}:{t.minute:02d}"
        _apply_wake_time(routine, wake_str)
        db.add(routine)
        db.commit()
        context.user_data["onboarding_step"] = "bed"
        await update.message.reply_text("Спасибо. Во сколько обычно ложитесь спать? (HH:MM)")
        return True

    if step == "bed":
        if text.strip().lower() in {"skip", "later", "пропустить", "потом", "не знаю"}:
            context.user_data["onboarding_step"] = "goal"
            await update.message.reply_text("Какой сейчас главный фокус? (работа, фитнес, семья, учеба, другое)")
            return True
        t = _parse_time_value(text) or None
        if not t:
            await update.message.reply_text("Введите время в формате HH:MM, например 23:30")
            return True
        bed_str = f"{t.hour:02d}:{t.minute:02d}"
        routine.sleep_target_bedtime = bed_str
        db.add(routine)
        db.commit()
        context.user_data["onboarding_step"] = "goal"
        await update.message.reply_text("Какой сейчас главный фокус? (работа, фитнес, семья, учеба, другое)")
        return True

    if step == "goal":
        answer = text.strip().lower()
        if answer in {"skip", "later", "пропустить", "потом", "не знаю"}:
            user.onboarded = True
            db.add(user)
            db.commit()
            context.user_data.pop("onboarding_step", None)
            await update.message.reply_text("Готово. Теперь можно отправлять задачи или использовать /morning.")
            return True
        suggestions = suggest_routine_steps(
            answer,
            settings.OPENAI_API_KEY,
            settings.OPENAI_CHAT_MODEL,
        ) or _suggest_routine_steps(answer)
        context.user_data["suggested_steps"] = suggestions
        context.user_data["onboarding_step"] = "suggest"
        suggestion_text = ", ".join(suggestions)
        await update.message.reply_text(
            "Предлагаемая рутина: " + suggestion_text + "\n"
            "Ответьте «да», чтобы принять, или пришлите свой список."
        )
        return True

    if step == "suggest":
        answer = text.strip().lower()
        if answer in {"skip", "none", "пропустить", "нет"}:
            user.onboarded = True
            db.add(user)
            db.commit()
            context.user_data.pop("onboarding_step", None)
            await update.message.reply_text("Готово. Теперь можно отправлять задачи или использовать /morning.")
            return True

        if answer in {"yes", "ok", "okay", "sure", "y", "да", "ок", "хорошо"}:
            steps = context.user_data.get("suggested_steps") or ["Стакан воды", "Растяжка", "Завтрак", "План дня"]
        else:
            steps = _split_items(text)
            if not steps:
                steps = context.user_data.get("suggested_steps") or ["Стакан воды", "Растяжка", "Завтрак", "План дня"]

        existing = crud.list_routine_steps(db, user.id, active_only=False)
        position = len(existing) + 1
        offset = 0
        for title in steps:
            crud.add_routine_step(
                db,
                user.id,
                title=title,
                offset_min=offset,
                duration_min=10,
                kind="morning",
                position=position,
            )
            position += 1
            offset += 10

        user.onboarded = True
        db.add(user)
        db.commit()
        context.user_data.pop("onboarding_step", None)
        await update.message.reply_text("Рутина сохранена. Используйте /morning, чтобы увидеть ее.")
        return True

    return False


async def _process_user_text(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> None:
    routine = crud.get_routine(db, user.id)
    ai_enabled = bool(settings.OPENAI_API_KEY)

    if await _handle_pending_action(text, update, context, db, user, routine):
        return

    parsed_command = _parse_command_text(text)
    if parsed_command:
        name, args = parsed_command
        if name in {"delete", "done", "unschedule"} and not args:
            await _prompt_task_selection(name, update, context, db, user, routine)
            return
        if name in {"delete", "done", "unschedule"}:
            ids = _extract_task_ids(" ".join(args))
            if ids:
                await _apply_task_actions(name, ids, update, db, user)
                return
        handled = await _run_command_by_name(name, args, update, context)
        if handled:
            return

    if _looks_like_greeting(text):
        await update.message.reply_text("Привет! Чем помочь?")
        return

    if ai_enabled:
        data = parse_intent(text, settings.OPENAI_API_KEY, settings.OPENAI_CHAT_MODEL)
        if data:
            handled = await _handle_ai_intent(data, text, update, context, db, user)
            if handled:
                return

    lower = text.lower()
    if any(word in lower for word in ["удали", "удалить", "delete", "стереть", "убери задачу"]):
        ids = _extract_task_ids(text)
        if ids:
            await _apply_task_actions("delete", ids, update, db, user)
        else:
            await _prompt_task_selection("delete", update, context, db, user, routine)
        return

    if any(word in lower for word in ["сделано", "готово", "закрыть", "завершить", "done"]):
        ids = _extract_task_ids(text)
        if ids:
            await _apply_task_actions("done", ids, update, db, user)
        else:
            await _prompt_task_selection("done", update, context, db, user, routine)
        return

    if any(word in lower for word in ["убери из расписания", "сними с плана", "перенеси в бэклог", "unschedule"]):
        ids = _extract_task_ids(text)
        if ids:
            await _apply_task_actions("unschedule", ids, update, db, user)
        else:
            await _prompt_task_selection("unschedule", update, context, db, user, routine)
        return

    if _is_autoplan_request(text):
        args = _parse_autoplan_args(text)
        await _run_command_by_name("autoplan", args, update, context)
        return

    if _is_plan_request(text):
        day = _detect_day_from_text(text, _now_local_naive())
        ensure_day_anchors(db, user.id, day, routine)
        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
        await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine))
        return

    if _is_breakfast_request(text):
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]
        suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
        if not pantry_names:
            await update.message.reply_text("В кладовой пусто. Добавьте продукты через /pantry add <продукт>.")
            return
        if not suggestions:
            await update.message.reply_text("Нет подходящих рецептов. Добавьте больше продуктов.")
            return
        lines = ["Идеи для завтрака:"]
        for s in suggestions:
            if s["missing"]:
                missing = ", ".join(s["missing"])
                lines.append(f"- {s['name']} (не хватает: {missing})")
            else:
                lines.append(f"- {s['name']} (все есть)")
        await update.message.reply_text("\n".join(lines))
        return

    items = _extract_routine_items(text)
    if items:
        existing = crud.list_routine_steps(db, user.id, active_only=False)
        position = len(existing) + 1
        offset = 0
        for title in items:
            crud.add_routine_step(
                db,
                user.id,
                title=title,
                offset_min=offset,
                duration_min=10,
                kind="morning",
                position=position,
            )
            position += 1
            offset += 10

        await update.message.reply_text(f"Добавлено шагов: {len(items)}. Используйте /morning для просмотра.")
        return

    if not _should_create_task(text):
        if ai_enabled:
            context_prompt = _build_assistant_context(db, user)
            history = _get_chat_history(context)
            reply = chat_reply(
                text,
                settings.OPENAI_API_KEY,
                settings.OPENAI_CHAT_MODEL,
                system_prompt=_assistant_system_prompt(),
                context_prompt=context_prompt,
                history=history,
            )
            if reply:
                _append_chat_history(context, "user", text)
                _append_chat_history(context, "assistant", reply)
                await update.message.reply_text(reply)
                return
            await update.message.reply_text("Не удалось получить ответ от ИИ. Попробуйте еще раз.")
            return
        await update.message.reply_text("Если нужно создать задачу, скажите: «создай задачу ...»")
        return

    parsed = parse_quick_task(text, _now_local_naive())
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

    when = parsed.due_at.strftime("%Y-%m-%d %H:%M") if parsed.due_at else "(без срока)"
    checklist_info = f" Чек-лист: {len(parsed.checklist_items)} пунктов." if parsed.checklist_items else ""
    await update.message.reply_text(f"Добавлено: {task.title}. Срок: {when}.{checklist_info}")


async def _handle_ai_intent(
    data: dict,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> bool:
    intent = (data.get("intent") or "").lower()
    if intent == "routine":
        items = data.get("items") or []
        if isinstance(items, str):
            items = _split_items(items)
        if not items:
            return False
        existing = crud.list_routine_steps(db, user.id, active_only=False)
        position = len(existing) + 1
        offset = 0
        for title in items:
            crud.add_routine_step(
                db,
                user.id,
                title=title,
                offset_min=offset,
                duration_min=10,
                kind="morning",
                position=position,
            )
            position += 1
            offset += 10
        await update.message.reply_text(f"Добавлено шагов рутины: {len(items)}. Используйте /morning для просмотра.")
        return True

    if intent in {"pantry_add", "pantry_remove"}:
        items = data.get("items") or []
        if isinstance(items, str):
            items = _split_items(items)
            items = [{"name": i, "quantity": None} for i in items]
        if not items:
            return False
        if intent == "pantry_add":
            for item in items:
                name = str(item.get("name", "")).strip()
                qty = item.get("quantity")
                if name:
                    crud.upsert_pantry_item(db, user.id, name=name, quantity=qty)
            await update.message.reply_text("Продукты обновлены.")
            return True
        for item in items:
            name = str(item.get("name", "")).strip()
            if name:
                crud.remove_pantry_item(db, user.id, name=name)
        await update.message.reply_text("Продукты обновлены.")
        return True

    if intent == "workout_set":
        weekday = data.get("weekday")
        title = (data.get("title") or "").strip()
        details = data.get("details")
        if weekday is None or not title:
            return False
        try:
            weekday_int = int(weekday)
        except ValueError:
            return False
        if weekday_int < 0 or weekday_int > 6:
            return False
        crud.set_workout_plan(db, user.id, weekday_int, title=title, details=details)
        await update.message.reply_text(f"План тренировки сохранен для дня {weekday_int}: {title}")
        return True

    if intent == "breakfast":
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]
        suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
        if not pantry_names:
            await update.message.reply_text("В кладовой пусто. Добавьте продукты через /pantry add <продукт>.")
            return True
        if not suggestions:
            await update.message.reply_text("Нет подходящих рецептов. Добавьте больше продуктов.")
            return True
        lines = ["Идеи для завтрака:"]
        for s in suggestions:
            if s["missing"]:
                missing = ", ".join(s["missing"])
                lines.append(f"- {s['name']} (не хватает: {missing})")
            else:
                lines.append(f"- {s['name']} (все есть)")
        await update.message.reply_text("\n".join(lines))
        return True

    if intent == "plan":
        routine = crud.get_routine(db, user.id)
        day = _now_local_naive().date()
        ensure_day_anchors(db, user.id, day, routine)
        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
        await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine))
        return True

    if intent == "command":
        name = str(data.get("name", "")).strip().lower()
        args = data.get("args") or []
        if isinstance(args, str):
            args = args.split()
        if not isinstance(args, list):
            args = []
        if name in {"delete", "done", "unschedule"} and not args:
            routine = crud.get_routine(db, user.id)
            await _prompt_task_selection(name, update, context, db, user, routine)
            return True
        if name in {"delete", "done", "unschedule"}:
            ids = _extract_task_ids(" ".join(str(a) for a in args))
            if ids:
                await _apply_task_actions(name, ids, update, db, user)
                return True
        return await _run_command_by_name(name, [str(a) for a in args], update, context)

    if intent == "task":
        text = data.get("text") or original_text
        if not _should_create_task(original_text):
            return False
        parsed = parse_quick_task(text, _now_local_naive())
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
        when = parsed.due_at.strftime("%Y-%m-%d %H:%M") if parsed.due_at else "(без срока)"
        checklist_info = f" Чек-лист: {len(parsed.checklist_items)} пунктов." if parsed.checklist_items else ""
        await update.message.reply_text(f"Добавлено: {task.title}. Срок: {when}.{checklist_info}")
        return True

    return False


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return
    with get_db_session() as db:
        user = await _get_active_user(update, context, db)
        if not user:
            return
        if await _handle_onboarding_text(text, update, context, db, user):
            return
        await _process_user_text(text, update, context, db, user)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice:
        return
    with get_db_session() as db:
        user = await _get_active_user(update, context, db)
        if not user:
            return

        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "voice.ogg"
            await file.download_to_drive(custom_path=str(path))
            transcript = transcribe_audio(
                str(path),
                settings.OPENAI_API_KEY,
                model=settings.OPENAI_TRANSCRIBE_MODEL,
                language=settings.OPENAI_TRANSCRIBE_LANGUAGE,
            )

        if not transcript:
            await update.message.reply_text(
                "Голос получен, но распознавание не включено. "
                "Установите OPENAI_API_KEY или отправьте текст."
            )
            return

        await update.message.reply_text(f"Распознано: {transcript}")
        if await _handle_onboarding_text(transcript, update, context, db, user):
            return
        await _process_user_text(transcript, update, context, db, user)

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
            if not getattr(user, "is_active", True):
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
    with get_db_session() as db:
        user = await _get_user(update, db)
        if not user.is_active:
            user.is_active = True
            db.add(user)
            db.commit()
        if not user.onboarded:
            await update.message.reply_text("Добро пожаловать! Давайте настроим рутину.")
            await _start_onboarding(update, context)
            return

    msg = (
        "Дневной планировщик.\n\n"
        "Команды:\n"
        "/me - показать user_id\n"
        "/todo <минуты> <текст> - добавить задачу в бэклог\n"
        "/capture <текст> - быстрое добавление с датой/временем\n"
        "/call <имя> [заметки] - зафиксировать звонок и создать фоллоу-ап\n"
        "/plan [YYYY-MM-DD] - показать план\n"
        "/autoplan <дни> [YYYY-MM-DD] - распланировать бэклог\n"
        "/morning - утренняя рутина на сегодня\n"
        "/routine_add <смещение> <длительность> <название> [| тип]\n"
        "/routine_list - список шагов рутины\n"
        "/routine_del <id> - удалить шаг рутины\n"
        "/pantry add|remove|list <продукт>\n"
        "/breakfast - предложить завтрак из продуктов\n"
        "/workout today|show|set|clear|list ...\n"
        "/cabinet - кабинет и статистика\n"
        "/login - включить аккаунт\n"
        "/logout - выключить аккаунт\n"
        "/slots <id> [YYYY-MM-DD] - доступные слоты\n"
        "/place <id> <slot#> [HH:MM] - поставить в слот\n"
        "/schedule <id> <HH:MM> [YYYY-MM-DD] - запланировать по времени\n"
        "/unschedule <id> - вернуть в бэклог\n"
        "/done <id> - отметить выполненной\n"
        "/delete <id> - удалить задачу\n"
    )
    await update.message.reply_text(msg)


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        api_key_hint = " (нужен X-API-Key)" if settings.API_KEY else ""
        await update.message.reply_text(
            "Информация о пользователе:\n"
            f"- user_id: {user.id}\n"
            f"- telegram_chat_id: {user.telegram_chat_id}\n"
            f"- часовой пояс: {settings.TZ}\n\n"
            f"Заголовок API: X-User-Id = user_id{api_key_hint}"
        )


async def cmd_cabinet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        steps = crud.list_routine_steps(db, user.id, active_only=False)
        pantry = crud.list_pantry_items(db, user.id)
        workouts = crud.list_workout_plans(db, user.id)
        status = "активен" if user.is_active else "неактивен"
        onboarded = "да" if user.onboarded else "нет"
        await update.message.reply_text(
            "Кабинет:\n"
            f"- статус: {status}\n"
            f"- онбординг: {onboarded}\n"
            f"- шагов рутины: {len(steps)}\n"
            f"- продуктов в кладовой: {len(pantry)}\n"
            f"- планов тренировок: {len(workouts)}"
        )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        if user.is_active:
            await update.message.reply_text("Аккаунт уже активен.")
            return
        user.is_active = True
        db.add(user)
        db.commit()
        await update.message.reply_text("С возвращением.")
        if not user.onboarded:
            await _start_onboarding(update, context)


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_user(update, db)
        user.is_active = False
        db.add(user)
        db.commit()
        context.user_data.pop("onboarding_step", None)
        await update.message.reply_text("Вы вышли. Используйте /login, чтобы включить аккаунт.")


def _render_day_plan(tasks, backlog, day: dt.date, routine) -> str:
    lines = []
    lines.append(f"План на {day.isoformat()}:\n")

    if tasks:
        for i, t in enumerate(tasks, start=1):
            s = t.planned_start.strftime("%H:%M")
            e = t.planned_end.strftime("%H:%M")
            extra = ""
            if t.kind == "workout":
                extra = f" (дорога: {routine.workout_travel_oneway_min}м в одну сторону)"
            tag = f" [{t.kind}]" if t.kind else ""
            status = "[x]" if t.is_done else "[ ]"
            lines.append(f"{status} {i}) {s}-{e} {t.title}{tag} (id={t.id}){extra}")
    else:
        lines.append("(нет запланированных задач)")

    if backlog:
        lines.append("\nБэклог:")
        for i, t in enumerate(backlog, start=1):
            mins = task_display_minutes(t, routine)
            lines.append(f"[ ] {i}) {t.title} ~ {mins}м (id={t.id})")
        lines.append("\nПодсказка: /autoplan 1")

    return "\n".join(lines)


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_arg = context.args[0] if context.args else None
    if date_arg:
        try:
            day = normalize_date_str(date_arg)
        except ValueError:
            await update.message.reply_text("Дата должна быть в формате YYYY-MM-DD")
            return
    else:
        day = _now_local_naive().date()

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)

        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]

        await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine))


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    day = _now_local_naive().date()
    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        routine_tasks = [t for t in tasks if t.task_type == "system" and (t.idempotency_key or "").startswith("routine:")]

        if not routine_tasks:
            await update.message.reply_text("Пока нет шагов рутины. Используйте /routine_add для добавления.")
            return

        routine_tasks.sort(key=lambda t: t.planned_start or dt.datetime.max)
        lines = ["Утренняя рутина:"]
        for t in routine_tasks:
            s = t.planned_start.strftime("%H:%M") if t.planned_start else "?"
            e = t.planned_end.strftime("%H:%M") if t.planned_end else "?"
            lines.append(f"- {s}-{e} {t.title} (id={t.id})")

        await update.message.reply_text("\n".join(lines))

async def cmd_routine_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Использование: /routine_add <смещение_мин> <длительность_мин> <название> [| тип]")
        return

    try:
        offset_min = int(context.args[0])
        duration_min = int(context.args[1])
    except ValueError:
        await update.message.reply_text("смещение_мин и длительность_мин должны быть числами")
        return

    rest = " ".join(context.args[2:]).strip()
    title = rest
    kind = "morning"
    if "|" in rest:
        title, kind = [p.strip() for p in rest.split("|", 1)]

    if not title:
        await update.message.reply_text("Название не может быть пустым")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
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

    await update.message.reply_text(f"Добавлен шаг рутины: {step.title} (id={step.id})")


async def cmd_routine_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        steps = crud.list_routine_steps(db, user.id, active_only=False)
        if not steps:
            await update.message.reply_text("Пока нет шагов рутины. Используйте /routine_add.")
            return

        lines = ["Шаги рутины:"]
        for s in steps:
            lines.append(
                f"- id={s.id} смещение={s.offset_min}м длительность={s.duration_min}м тип={s.kind} название={s.title}"
            )
        await update.message.reply_text("\n".join(lines))


async def cmd_routine_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /routine_del <id_шага>")
        return

    try:
        step_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id_шага должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        ok = crud.delete_routine_step(db, user.id, step_id)
        if not ok:
            await update.message.reply_text("Шаг рутины не найден")
            return

    await update.message.reply_text(f"Шаг рутины удален (id={step_id})")


async def cmd_pantry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /pantry add|remove|list <продукт>")
        return

    action = context.args[0].lower()
    rest = " ".join(context.args[1:]).strip()

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return

        if action in {"list", "ls"}:
            items = crud.list_pantry_items(db, user.id)
            if not items:
                await update.message.reply_text("В кладовой пусто. Добавьте продукты через /pantry add <продукт>")
                return
            lines = ["Кладовая:"]
            for item in items:
                qty = f" ({item.quantity})" if item.quantity else ""
                lines.append(f"- {item.name}{qty}")
            await update.message.reply_text("\n".join(lines))
            return

        if action == "add":
            if not rest:
                await update.message.reply_text("Использование: /pantry add <продукт>[=кол-во]")
                return
            name = rest
            quantity = None
            if "=" in rest:
                name, quantity = [p.strip() for p in rest.split("=", 1)]
            elif ":" in rest:
                name, quantity = [p.strip() for p in rest.split(":", 1)]
            crud.upsert_pantry_item(db, user.id, name=name, quantity=quantity)
            await update.message.reply_text(f"Добавлено в кладовую: {name}")
            return

        if action in {"remove", "del", "delete"}:
            if not rest:
                await update.message.reply_text("Использование: /pantry remove <продукт>")
                return
            ok = crud.remove_pantry_item(db, user.id, name=rest)
            if not ok:
                await update.message.reply_text("Продукт не найден")
                return
            await update.message.reply_text(f"Удалено из кладовой: {rest}")
            return

    await update.message.reply_text("Использование: /pantry add|remove|list <продукт>")


async def cmd_breakfast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]

    suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
    if not pantry_names:
        await update.message.reply_text("В кладовой пусто. Добавьте продукты через /pantry add <продукт>")
        return
    if not suggestions:
        await update.message.reply_text("Нет подходящих рецептов. Добавьте больше продуктов.")
        return

    lines = ["Идеи для завтрака:"]
    for s in suggestions:
        if s["missing"]:
            missing = ", ".join(s["missing"])
            lines.append(f"- {s['name']} (не хватает: {missing})")
        else:
            lines.append(f"- {s['name']} (все есть)")
    await update.message.reply_text("\n".join(lines))

async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /workout today|show|set|clear|list ...")
        return

    action = context.args[0].lower()
    args = context.args[1:]

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return

        if action == "today":
            weekday = _now_local_naive().weekday()
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text("Плана тренировки на сегодня нет.")
                return
            text = plan.details or "(без подробностей)"
            await update.message.reply_text(f"Тренировка сегодня: {plan.title}\n{text}")
            return

        if action == "show":
            if not args:
                await update.message.reply_text("Использование: /workout show <день_недели>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Неверный день недели. Используйте 0-6 или пн..вс")
                return
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text("Плана тренировки на этот день нет.")
                return
            text = plan.details or "(без подробностей)"
            await update.message.reply_text(f"План тренировки: {plan.title}\n{text}")
            return

        if action == "set":
            if len(args) < 2:
                await update.message.reply_text("Использование: /workout set <день> <название> | <детали>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Неверный день недели. Используйте 0-6 или пн..вс")
                return
            rest = " ".join(args[1:])
            title = rest
            details = None
            if "|" in rest:
                title, details = [p.strip() for p in rest.split("|", 1)]
            plan = crud.set_workout_plan(db, user.id, weekday, title=title, details=details)
            await update.message.reply_text(f"План тренировки сохранен для дня {plan.weekday}: {plan.title}")
            return

        if action == "clear":
            if not args:
                await update.message.reply_text("Использование: /workout clear <день>")
                return
            weekday = _parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text("Неверный день недели. Используйте 0-6 или пн..вс")
                return
            ok = crud.clear_workout_plan(db, user.id, weekday)
            if not ok:
                await update.message.reply_text("План тренировки не найден")
                return
            await update.message.reply_text("План тренировки удален")
            return

        if action == "list":
            plans = crud.list_workout_plans(db, user.id)
            if not plans:
                await update.message.reply_text("Планов тренировок пока нет. Используйте /workout set.")
                return
            lines = ["Планы тренировок:"]
            for plan in plans:
                lines.append(f"- день {plan.weekday}: {plan.title}")
            await update.message.reply_text("\n".join(lines))
            return

    await update.message.reply_text("Использование: /workout today|show|set|clear|list ...")


async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /capture <текст>")
        return

    text = " ".join(context.args).strip()
    now = _now_local_naive()
    parsed = parse_quick_task(text, now)

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
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

    when = parsed.due_at.strftime("%Y-%m-%d %H:%M") if parsed.due_at else "(без срока)"
    checklist_info = f" Чек-лист: {len(parsed.checklist_items)} пунктов." if parsed.checklist_items else ""
    await update.message.reply_text(f"Добавлено: {task.title}. Срок: {when}.{checklist_info}")


async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /call <имя> [заметки]")
        return

    name = context.args[0].strip()
    notes = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    now = _now_local_naive()
    due_day = now.date() + dt.timedelta(days=max(0, settings.CALL_FOLLOWUP_DAYS))
    due_at = dt.datetime.combine(due_day, dt.time(9, 0))

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        task = crud.create_task_fields(
            db,
            user.id,
            title=f"Фоллоу-ап по {name}",
            notes=notes,
            due_at=due_at,
            priority=2,
            estimate_minutes=15,
            task_type="user",
            schedule_source="manual",
            idempotency_key=_idempotency_key(update),
        )
        crud.add_checklist_items(db, task.id, [f"Отправить резюме {name}"])

    await update.message.reply_text(f"Звонок зафиксирован. Создан фоллоу-ап (id={task.id}).")

async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /todo <минуты> <текст>")
        return

    try:
        estimate = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Минуты должны быть числом. Пример: /todo 30 разобрать почту")
        return

    title = " ".join(context.args[1:]).strip()
    if not title:
        await update.message.reply_text("Название не может быть пустым.")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
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
        await update.message.reply_text(f"Создано. Задача id={task.id} добавлена в бэклог.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /done <id> [id2 ...]")
        return
    ids = _extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        await _apply_task_actions("done", ids, update, db, user)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /delete <id> [id2 ...]")
        return

    ids = _extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        await _apply_task_actions("delete", ids, update, db, user)


async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /unschedule <id> [id2 ...]")
        return

    ids = _extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        await _apply_task_actions("unschedule", ids, update, db, user)


async def cmd_autoplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /autoplan <дни> [YYYY-MM-DD]")
        return

    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text("дни должны быть числом")
        return

    start_date = None
    if len(context.args) >= 2:
        try:
            start_date = normalize_date_str(context.args[1])
        except ValueError:
            await update.message.reply_text("Дата должна быть в формате YYYY-MM-DD")
            return

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        result = autoplan_days(db, user.id, routine, days=days, start_date=start_date)

    suffix = f" {start_date.isoformat()}" if start_date else ""
    await update.message.reply_text(f"Автопланирование завершено: {result}\nПлан: /plan{suffix}")


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
        await update.message.reply_text("Использование: /slots <id_задачи> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id_задачи должен быть числом")
        return

    date_arg = context.args[1] if len(context.args) >= 2 else None

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Через /slots можно планировать только пользовательские задачи.")
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text("Дата должна быть в формате YYYY-MM-DD")
                return
        else:
            day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        text = format_gap_options(task, gaps, routine, day)

    await update.message.reply_text(text)

async def cmd_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /place <id_задачи> <слот#> [HH:MM]")
        return

    try:
        task_id = int(context.args[0])
        slot_idx = int(context.args[1])
    except ValueError:
        await update.message.reply_text("id_задачи и слот# должны быть числами")
        return

    hhmm = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Через /place можно планировать только пользовательские задачи.")
            return

        day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        if slot_idx < 1 or slot_idx > len(gaps):
            await update.message.reply_text("Неверный номер слота. Используйте /slots <id>.")
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
            await update.message.reply_text("Этот слот не подходит. Используйте /slots снова.")
            return

        start = earliest
        if hhmm:
            try:
                t = parse_hhmm(hhmm)
            except Exception:
                await update.message.reply_text("Время должно быть HH:MM, например 21:30")
                return
            candidate = dt.datetime.combine(day, t)
            if candidate < earliest or candidate > latest:
                await update.message.reply_text(
                    f"Время вне слота. Используйте {earliest.strftime('%H:%M')}-{latest.strftime('%H:%M')}"
                )
                return
            start = candidate

        end = start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(
        f"Запланировано (id={task_id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})"
    )


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /schedule <id_задачи> <HH:MM> [YYYY-MM-DD]")
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id_задачи должен быть числом")
        return

    hhmm = context.args[1]
    date_arg = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await _get_ready_user(update, context, db)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if task.task_type != "user":
            await update.message.reply_text("Через /schedule можно планировать только пользовательские задачи.")
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text("Дата должна быть в формате YYYY-MM-DD")
                return
        else:
            day = task.planned_start.date() if task.planned_start else _now_local_naive().date()

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
            await update.message.reply_text("Время не подходит под доступные слоты. Используйте /slots <id>.")
            return

        end = desired_start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=desired_start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(
        f"Запланировано (id={task_id}) {desired_start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})"
    )


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
    app.add_handler(CommandHandler("cabinet", cmd_cabinet))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("unschedule", cmd_unschedule))
    app.add_handler(CommandHandler("slots", cmd_slots))
    app.add_handler(CommandHandler("place", cmd_place))
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    app.job_queue.run_repeating(reminder_job, interval=60, first=15)

    logger.info("Bot started")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
