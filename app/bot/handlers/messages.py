from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_active_user as _get_active_user, get_db_session
from app.bot.handlers.core import cmd_cabinet, cmd_login, cmd_logout, cmd_me, cmd_setup, cmd_start
from app.bot.handlers.health import cmd_habit, cmd_health, cmd_workout
from app.bot.handlers.location import cmd_task_location
from app.bot.handlers.pantry import cmd_breakfast, cmd_pantry
from app.bot.handlers.routine import (
    cmd_morning,
    cmd_routine_add,
    cmd_routine_del,
    cmd_routine_list,
    handle_onboarding_text as _handle_onboarding_text,
)
from app.bot.handlers.tasks import (
    apply_task_actions as _apply_task_actions,
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
    handle_pending_action as _handle_pending_action,
    handle_pending_conflict as _handle_pending_conflict,
    handle_pending_schedule as _handle_pending_schedule,
    handle_pending_task as _handle_pending_task,
    handle_task_request as _handle_task_request,
    prompt_task_selection as _prompt_task_selection,
)
from app.bot.parsing.commands import parse_command_text as _parse_command_text, parse_yes_no as _parse_yes_no
from app.bot.parsing.text import extract_task_ids as _extract_task_ids, split_items as _split_items
from app.bot.parsing.time import (
    DATE_TOKEN_RE,
    RUS_WEEKDAY_MAP,
    _detect_relative_day,
    _extract_dates_from_text,
    _format_date_list,
)
from app.bot.rendering.tasks import render_day_plan as _render_day_plan
from app.bot.utils import now_local_naive as _now_local_naive
from app.services.ai_chat import chat_reply
from app.services.ai_intent import parse_intent
from app.services.ai_transcribe import transcribe_audio
from app.services.autoplan import ensure_day_anchors
from app.services.meal_suggest import suggest_meals
from app.services.quick_capture import parse_quick_task
from app.settings import settings



def _extract_person_name(text: str) -> str | None:
    m = re.search(r"\b(?:с|со)\s+([А-Яа-яA-Za-z][^,.;!?]+)", text)
    if not m:
        return None
    name = m.group(1).strip()
    return name[:80] if name else None


def _detect_suggestion(text: str) -> dict | None:
    lower = text.lower()
    if any(x in lower for x in ["звонок", "созвон", "созвонился", "созвонилась", "call"]):
        name = _extract_person_name(text)
        return {"type": "followup", "name": name}
    if any(x in lower for x in ["встреча", "митинг", "meeting", "переговор"]):
        return {"type": "prep", "raw": text}
    return None


async def _handle_pending_suggestion(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> bool:
    pending = context.user_data.get("pending_suggestion")
    if not pending:
        return False
    answer = _parse_yes_no(text)
    if answer is None:
        await update.message.reply_text("Ответьте «да» или «нет».")
        return True
    context.user_data.pop("pending_suggestion", None)
    if not answer:
        await update.message.reply_text("Ок, без задачи.")
        return True

    now = _now_local_naive()
    if pending.get("type") == "followup":
        name = pending.get("name") or "контакту"
        due_day = now.date() + dt.timedelta(days=max(0, settings.CALL_FOLLOWUP_DAYS))
        due_at = dt.datetime.combine(due_day, dt.time(9, 0))
        task = crud.create_task_fields(
            db,
            user.id,
            title=f"Фоллоу-ап по {name}",
            notes=None,
            due_at=due_at,
            priority=2,
            estimate_minutes=15,
            task_type="user",
            schedule_source="assistant",
            idempotency_key=None,
        )
        crud.add_checklist_items(db, task.id, [f"Отправить резюме {name}"])
        await update.message.reply_text(f"Создан фоллоу-ап (id={task.id}).")
        return True

    if pending.get("type") == "prep":
        raw = pending.get("raw") or ""
        parsed = parse_quick_task(raw, now)
        due_at = None
        if parsed.due_at:
            due_at = parsed.due_at - dt.timedelta(hours=1)
            if due_at < now:
                due_at = parsed.due_at
        else:
            due_at = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(10, 0))
        task = crud.create_task_fields(
            db,
            user.id,
            title="Подготовиться к встрече",
            notes=None,
            due_at=due_at,
            priority=2,
            estimate_minutes=30,
            task_type="user",
            schedule_source="assistant",
            idempotency_key=None,
        )
        await update.message.reply_text(f"Создана задача подготовки (id={task.id}).")
        return True

    return True


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


def _looks_like_delete_by_date(text: str, now: dt.datetime) -> bool:
    lower = text.lower()
    if not any(word in lower for word in ["удали", "удалить", "стереть", "убери", "delete", "clear"]):
        return False
    if _extract_dates_from_text(text, now):
        return True
    if _detect_relative_day(text, now):
        return True
    if MONTH_RE.search(text):
        return True
    return False


def _parse_clear_targets(text: str) -> list[str]:
    lower = text.lower()
    targets = []
    if any(word in lower for word in ["задач", "task", "tasks"]):
        targets.append("tasks")
    if any(word in lower for word in ["рутин", "routine", "routine", "утрен"]):
        targets.append("routine")
    return targets


def _looks_like_clear_all(text: str) -> bool:
    lower = text.lower()
    has_clear = any(word in lower for word in ["удали", "удалить", "очисти", "очистить", "стереть", "clear", "wipe"])
    has_all = any(word in lower for word in ["все", "полностью", "целиком", "all", "everything"])
    return has_clear and has_all


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


def _format_clear_targets(targets: list[str]) -> str:
    parts = []
    if "tasks" in targets:
        parts.append("все задачи")
    if "routine" in targets:
        parts.append("все шаги рутины")
    if len(parts) == 2:
        return f"{parts[0]} и {parts[1]}"
    if parts:
        return parts[0]
    return "все данные"


def _build_assistant_context(db, user) -> str:
    now = _now_local_naive()
    routine = crud.get_routine(db, user.id)
    day = now.date()
    tasks = crud.list_tasks_for_day(db, user.id, day)
    scheduled = [t for t in tasks if t.planned_start and not t.is_done]
    backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
    scheduled.sort(key=lambda t: t.planned_start)

    display_name = user.full_name or "пользователь"
    lines = [
        f"Пользователь: {display_name}",
        f"Фокус: {user.primary_focus or 'не задан'}",
        f"Дата и время: {now.strftime('%Y-%m-%d %H:%M')} ({user.timezone or settings.TZ})",
        f"Цели сна: подъем {routine.sleep_target_wakeup}, сон {routine.sleep_target_bedtime}",
        f"Рабочий день: {routine.workday_start}-{routine.workday_end}",
        f"Ограничение по времени: {routine.latest_task_end or 'нет'}",
        f"Буфер между задачами: {routine.task_buffer_after_min} мин",
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
        "Ты - русскоязычный ИИ-ассистент для бизнеса (в стиле Джарвис), "
        "помогаешь планировать день, отвечаешь на вопросы, предлагаешь идеи и уточнения. "
        "Будь кратким, дружелюбным и практичным. "
        "Если для ответа нужна дополнительная информация, задай уточняющий вопрос. "
        "Не создавай задачи сам - предложи пользователю сказать: <создай задачу ...>, "
        "если это уместно."
    )


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
        "health": cmd_health,
        "habit": cmd_habit,
        "workout": cmd_workout,
        "task_location": cmd_task_location,
        "delay": cmd_delay,
        "cabinet": cmd_cabinet,
        "setup": cmd_setup,
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


async def _prompt_clear_all(
    targets: list[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not targets:
        await update.message.reply_text(
            "Что удалить? Напишите, например: удалить все задачи, удалить все шаги рутины."
        )
        return
    context.user_data["pending_clear"] = {"targets": targets}
    message = _format_clear_targets(targets)
    await update.message.reply_text(f"Подтвердите: удалить {message}? (да/нет)")


async def _handle_pending_clear(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> bool:
    pending = context.user_data.get("pending_clear")
    if not pending:
        return False
    answer = _parse_yes_no(text)
    if answer is None:
        await update.message.reply_text("Ответьте <да> или <нет>.")
        return True
    context.user_data.pop("pending_clear", None)
    if not answer:
        await update.message.reply_text("Ок, ничего не удаляю.")
        return True

    targets = pending.get("targets") or []
    count_tasks = 0
    count_steps = 0
    if "tasks" in targets:
        count_tasks = crud.delete_all_tasks(db, user.id)
    if "routine" in targets:
        count_steps = crud.delete_all_routine_steps(db, user.id)
    await update.message.reply_text(
        f"Готово. Удалено задач: {count_tasks}, шагов рутины: {count_steps}."
    )
    return True


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

    if intent == "clear_all":
        raw_targets = data.get("targets") or []
        if isinstance(raw_targets, str):
            raw_targets = _split_items(raw_targets)
        targets: list[str] = []
        for item in raw_targets:
            token = str(item).lower()
            if "task" in token or "задач" in token:
                targets.append("tasks")
            if "rout" in token or "рут" in token or "утрен" in token:
                targets.append("routine")
        if not targets:
            targets = _parse_clear_targets(original_text)
        await _prompt_clear_all(targets, update, context)
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
        task_text = data.get("text") or original_text
        routine = crud.get_routine(db, user.id)
        return await _handle_task_request(
            task_text,
            update,
            context,
            db,
            user,
            routine,
            schedule_source="assistant",
            idempotency_key=_idempotency_key(update),
        )

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

    if await _handle_pending_conflict(text, update, context, db, user, routine):
        return

    if await _handle_pending_schedule(text, update, context, db, user):
        return

    if await _handle_pending_task(text, update, context, db, user, routine):
        return

    if await _handle_pending_action(text, update, context, db, user, routine):
        return

    if await _handle_pending_clear(text, update, context, db, user):
        return

    if await _handle_pending_suggestion(text, update, context, db, user):
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

    if _looks_like_delete_by_date(text, _now_local_naive()):
        now = _now_local_naive()
        dates = _extract_dates_from_text(text, now)
        if not dates:
            relative = _detect_relative_day(text, now)
            if relative:
                dates = [relative]
        if not dates:
            await update.message.reply_text("Не понял даты. Пример: удали задачи 30 и 31 декабря.")
            return
        count = crud.delete_tasks_by_dates(db, user.id, dates)
        dates_text = _format_date_list(dates)
        await update.message.reply_text(f"Удалено задач на даты: {dates_text}. Всего: {count}.")
        return

    if _looks_like_clear_all(text):
        targets = _parse_clear_targets(text)
        await _prompt_clear_all(targets, update, context)
        return

    suggestion = _detect_suggestion(text)
    if suggestion and not _should_create_task(text):
        context.user_data["pending_suggestion"] = suggestion
        if suggestion["type"] == "followup":
            await update.message.reply_text("Создать фоллоу-ап по звонку? (да/нет)")
            return
        if suggestion["type"] == "prep":
            await update.message.reply_text("Создать задачу подготовки к встрече? (да/нет)")
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
    await _handle_task_request(
        text,
        update,
        context,
        db,
        user,
        routine,
        schedule_source="manual",
        idempotency_key=_idempotency_key(update),
    )


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
