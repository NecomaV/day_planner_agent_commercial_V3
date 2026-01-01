
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
from app.bot.parsing.commands import parse_command_text as _parse_command_text
from app.bot.parsing.ru_reply import parse_reply
from app.bot.parsing.text import (
    extract_routine_items as _extract_routine_items,
    extract_task_ids as _extract_task_ids,
    split_items as _split_items,
)
from app.bot.parsing.time import (
    DATE_TOKEN_RE,
    MONTH_RE,
    _detect_relative_day,
    _extract_dates_from_text,
    _extract_task_timing,
    _format_date_list,
    resolve_date_ru,
)
from app.bot.rendering.keyboard import yes_no_cancel_keyboard, yes_no_keyboard
from app.bot.rendering.tasks import render_day_plan as _render_day_plan
from app.services.slots import task_display_minutes
from app.bot.throttle import throttle
from app.bot.utils import now_local_naive as _now_local_naive
from app.i18n.core import locale_for_user, t
from app.services.ai_guard import (
    breaker as _breaker,
    check_ai_quota,
    check_audio_limits,
    check_text_limit,
    check_transcribe_quota,
    record_ai_request,
    record_transcribe_seconds,
)
from app.services.ai_chat import chat_reply
from app.services.ai_intent import parse_intent
from app.services.ai_transcribe import transcribe_audio
from app.services.autoplan import ensure_day_anchors
from app.services.meal_suggest import suggest_meals
from app.services.quick_capture import parse_quick_task
from app.settings import settings


HEAVY_COMMANDS = {"plan", "autoplan", "call"}


def _idempotency_key(update: Update) -> str | None:
    if not update.message or not update.effective_chat:
        return None
    return f"tg:{update.effective_chat.id}:{update.message.message_id}"


def _user_key(user) -> str:
    return str(getattr(user, "id", None) or getattr(user, "telegram_chat_id", ""))


async def _acquire_heavy_lock(user, update: Update, *, text: str | None, locale: str):
    decision = throttle().check(_user_key(user), text=text, heavy=True)
    if not decision.allowed:
        if not decision.deduped:
            reason_key = decision.reason or "bot.throttle.cooldown"
            await update.message.reply_text(
                t(reason_key, locale=locale, retry_after=decision.retry_after),
                reply_markup=yes_no_cancel_keyboard(locale),
            )
        return None
    lock = throttle().get_lock(_user_key(user))
    if lock.locked():
        await update.message.reply_text(t("bot.throttle.busy", locale=locale))
        return None
    await lock.acquire()
    return lock


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
    locale = locale_for_user(user)
    flags = parse_reply(text)
    if flags.is_cancel:
        context.user_data.pop("pending_suggestion", None)
        await update.message.reply_text(t("suggestion.cancelled", locale=locale))
        return True
    answer = None
    if flags.is_yes and not flags.is_no:
        answer = True
    elif flags.is_no and not flags.is_yes:
        answer = False
    if answer is None:
        await update.message.reply_text(
            t("common.reply_yes_no", locale=locale),
            reply_markup=yes_no_keyboard(locale),
        )
        return True
    context.user_data.pop("pending_suggestion", None)
    if not answer:
        await update.message.reply_text(t("suggestion.skip", locale=locale))
        return True

    now = _now_local_naive()
    if pending.get("type") == "followup":
        name = pending.get("name") or t("suggestion.followup.default_name", locale=locale)
        due_day = now.date() + dt.timedelta(days=max(0, settings.CALL_FOLLOWUP_DAYS))
        due_at = dt.datetime.combine(due_day, dt.time(9, 0))
        task = crud.create_task_fields(
            db,
            user.id,
            title=t("suggestion.followup.title", locale=locale, name=name),
            notes=None,
            due_at=due_at,
            priority=2,
            estimate_minutes=15,
            task_type="user",
            schedule_source="assistant",
            idempotency_key=None,
        )
        crud.add_checklist_items(
            db,
            task.id,
            [t("suggestion.followup.checklist", locale=locale, name=name)],
        )
        await update.message.reply_text(
            t("suggestion.followup.created", locale=locale, task_id=task.id)
        )
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
            title=t("suggestion.prep.title", locale=locale),
            notes=None,
            due_at=due_at,
            priority=2,
            estimate_minutes=30,
            task_type="user",
            schedule_source="assistant",
            idempotency_key=None,
        )
        await update.message.reply_text(
            t("suggestion.prep.created", locale=locale, task_id=task.id)
        )
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
    if any(word in lower for word in ["рутин", "routine", "утрен"]):
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


def _format_clear_targets(targets: list[str], locale: str) -> str:
    parts = []
    if "tasks" in targets:
        parts.append(t("clear_all.targets.tasks", locale=locale))
    if "routine" in targets:
        parts.append(t("clear_all.targets.routine", locale=locale))
    if len(parts) == 2:
        return t("clear_all.targets.both", locale=locale, first=parts[0], second=parts[1])
    if parts:
        return parts[0]
    return t("clear_all.targets.all", locale=locale)


def _build_assistant_context(db, user) -> str:
    now = _now_local_naive()
    routine = crud.get_routine(db, user.id)
    day = now.date()
    tasks = crud.list_tasks_for_day(db, user.id, day)
    scheduled = [t for t in tasks if t.planned_start and not t.is_done]
    backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
    scheduled.sort(key=lambda t: t.planned_start)

    display_name = user.full_name or "user"
    lines = [
        f"User: {display_name}",
        f"Focus: {user.primary_focus or 'not set'}",
        f"Now: {now.strftime('%Y-%m-%d %H:%M')} ({user.timezone or settings.TZ})",
        f"Sleep targets: wake {routine.sleep_target_wakeup}, bed {routine.sleep_target_bedtime}",
        f"Workday: {routine.workday_start}-{routine.workday_end}",
        f"Latest task end: {routine.latest_task_end or 'none'}",
        f"Task buffer: {routine.task_buffer_after_min} min",
        f"Scheduled today: {len(scheduled)}",
    ]
    if scheduled:
        lines.append("Upcoming tasks:")
        for t in scheduled[:5]:
            when = t.planned_start.strftime("%H:%M")
            lines.append(f"- {when} {t.title} (id={t.id})")
    lines.append(f"Backlog: {len(backlog)}")
    return "\n".join(lines)


def _assistant_system_prompt(locale: str) -> str:
    language = "Russian" if locale.startswith("ru") else "English"
    return (
        "You are a professional business assistant. "
        "Answer user questions, offer concise suggestions, and ask for clarification when needed. "
        "Do not create tasks automatically; if a task is needed, suggest the user to say: <create task ...>. "
        f"Respond in {language}."
    )

def _remember_plan_context(context: ContextTypes.DEFAULT_TYPE, day: dt.date, tasks: list, backlog: list) -> None:
    context.user_data["last_plan_day"] = day.isoformat()
    context.user_data["last_plan_task_ids"] = [t.id for t in tasks] + [t.id for t in backlog]


def _sanitize_ai_reply(reply: str | None, locale: str) -> str | None:
    if not reply:
        return reply
    lower = reply.lower()
    if "доступ" in lower and "будущ" in lower:
        return t("ai.no_future", locale=locale)
    return reply


def _format_task_choice(task, routine, locale: str) -> str:
    if task.planned_start:
        when = task.planned_start.strftime("%H:%M")
    elif task.due_at:
        when = task.due_at.strftime("%Y-%m-%d %H:%M")
    else:
        when = t("tasks.choice.no_time", locale=locale)
    mins = task_display_minutes(task, routine)
    return t(
        "tasks.choice.line",
        locale=locale,
        task_id=task.id,
        title=task.title,
        when=when,
        minutes=mins,
    )


def _is_plan_request(text: str) -> bool:
    return bool(re.search(r"\b(план|расписание|график|розклад|plan)\b", text, flags=re.IGNORECASE))


def _is_tasks_request(text: str) -> bool:
    return bool(re.search(r"\b(задач|задачи|список задач|todo|tasks)\b", text, flags=re.IGNORECASE))


def _is_backlog_request(text: str) -> bool:
    return bool(re.search(r"\b(бэклог|беклог|backlog)\b", text, flags=re.IGNORECASE))


def _is_breakfast_request(text: str) -> bool:
    return bool(re.search(r"\b(завтрак|breakfast)\b", text, flags=re.IGNORECASE))


def _is_autoplan_request(text: str) -> bool:
    return bool(re.search(r"\b(автоплан|autoplan|распланируй|распланировать)\b", text, flags=re.IGNORECASE))


def _parse_reschedule_request(text: str, now: dt.datetime) -> dict | None:
    lower = text.lower()
    if not re.search(r"\b(перенеси|перенести|сдвинь|сдвинуть|перепланируй|перепланировать|запланируй|поставь)\b", lower):
        return None
    ids = _extract_task_ids(text)
    if not ids:
        return None
    date_hint, time_range, time_value, duration = _extract_task_timing(text, now)
    if time_range:
        start_time = time_range[0]
        end_time = time_range[1]
        duration = int((dt.datetime.combine(now.date(), end_time) - dt.datetime.combine(now.date(), start_time)).total_seconds() // 60)
    elif time_value:
        start_time = time_value
    else:
        return None
    date = date_hint or now.date()
    return {
        "task_id": ids[0],
        "date": date,
        "time": start_time,
        "duration": duration,
    }


def _normalize_action_text(text: str) -> str:
    cleaned = text.lower()
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
    cleaned = re.sub(
        r"\b(удали|удалить|удалилась|удалился|задача|задачи|пожалуйста|плиз|прошу|нужно|надо)\b",
        " ",
        cleaned,
    )
    return " ".join(cleaned.split()).strip()


def _match_tasks_by_title(tasks: list, query: str) -> list:
    if not query:
        return []
    hits = []
    for task in tasks:
        title = (task.title or "").lower()
        if query in title:
            hits.append(task)
    return hits


def _resolve_done_candidate(db, user, routine, now: dt.datetime) -> list:
    day = now.date()
    tasks = crud.list_tasks_for_day(db, user.id, day)
    scheduled = [t for t in tasks if t.planned_start and not t.is_done]
    if not scheduled:
        return []
    active = [t for t in scheduled if t.planned_end and t.planned_start <= now <= t.planned_end]
    if len(active) == 1:
        return active
    recent = [
        t
        for t in scheduled
        if t.planned_end and t.planned_end <= now and (now - t.planned_end).total_seconds() <= 3600
    ]
    if len(recent) == 1:
        return recent
    if len(scheduled) == 1:
        return scheduled
    return []


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


async def _run_command_with_throttle(
    name: str,
    args: list[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user,
) -> bool:
    if name not in HEAVY_COMMANDS:
        return await _run_command_by_name(name, args, update, context)

    locale = locale_for_user(user)
    lock = await _acquire_heavy_lock(user, update, text=" ".join(args) or None, locale=locale)
    if not lock:
        return True
    try:
        return await _run_command_by_name(name, args, update, context)
    finally:
        lock.release()


async def _prompt_clear_all(
    targets: list[str],
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    locale: str,
) -> None:
    if not targets:
        await update.message.reply_text(t("clear_all.usage", locale=locale))
        return
    context.user_data["pending_clear"] = {"targets": targets}
    message = _format_clear_targets(targets, locale)
    await update.message.reply_text(
        t("clear_all.confirm", locale=locale, targets=message),
        reply_markup=yes_no_keyboard(locale),
    )


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
    locale = locale_for_user(user)
    flags = parse_reply(text)
    if flags.is_cancel:
        context.user_data.pop("pending_clear", None)
        await update.message.reply_text(t("clear_all.cancelled", locale=locale))
        return True
    answer = None
    if flags.is_yes and not flags.is_no:
        answer = True
    elif flags.is_no and not flags.is_yes:
        answer = False
    if answer is None:
        await update.message.reply_text(
            t("common.reply_yes_no", locale=locale),
            reply_markup=yes_no_cancel_keyboard(locale),
        )
        return True
    context.user_data.pop("pending_clear", None)
    if not answer:
        await update.message.reply_text(t("clear_all.cancelled", locale=locale))
        return True

    targets = pending.get("targets") or []
    count_tasks = 0
    count_steps = 0
    if "tasks" in targets:
        count_tasks = crud.delete_all_tasks(db, user.id)
    if "routine" in targets:
        count_steps = crud.delete_all_routine_steps(db, user.id)
    await update.message.reply_text(
        t("clear_all.done", locale=locale, tasks=count_tasks, steps=count_steps)
    )
    return True


async def _handle_ai_intent(
    data: dict,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    *,
    locale: str,
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
        await update.message.reply_text(
            t("routine.added.bulk", locale=locale, count=len(items))
        )
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
            await update.message.reply_text(t("pantry.updated", locale=locale))
            return True
        for item in items:
            name = str(item.get("name", "")).strip()
            if name:
                crud.remove_pantry_item(db, user.id, name=name)
        await update.message.reply_text(t("pantry.updated", locale=locale))
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
        await update.message.reply_text(
            t("workout.set.saved", locale=locale, weekday=weekday_int, title=title)
        )
        return True

    if intent == "breakfast":
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]
        suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
        if not pantry_names:
            await update.message.reply_text(t("pantry.empty", locale=locale))
            return True
        if not suggestions:
            await update.message.reply_text(t("pantry.breakfast.none", locale=locale))
            return True
        lines = [t("pantry.breakfast.header", locale=locale)]
        for s in suggestions:
            if s["missing"]:
                missing = ", ".join(s["missing"])
                lines.append(
                    t(
                        "pantry.breakfast.item_missing",
                        locale=locale,
                        name=s["name"],
                        missing=missing,
                    )
                )
            else:
                lines.append(
                    t("pantry.breakfast.item_ready", locale=locale, name=s["name"])
                )
        await update.message.reply_text("\n".join(lines))
        return True

    if intent == "plan":
        routine = crud.get_routine(db, user.id)
        now = _now_local_naive()
        day = resolve_date_ru(original_text, now) or now.date()
        ensure_day_anchors(db, user.id, day, routine)
        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
        _remember_plan_context(context, day, scheduled, backlog)
        await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine, locale=locale))
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
        await _prompt_clear_all(targets, update, context, locale=locale)
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
        return await _run_command_with_throttle(name, [str(a) for a in args], update, context, user)

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
    locale = locale_for_user(user)
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
        handled = await _run_command_with_throttle(name, args, update, context, user)
        if handled:
            return

    if _looks_like_greeting(text):
        await update.message.reply_text(t("chat.greeting", locale=locale))
        return

    if _looks_like_delete_by_date(text, _now_local_naive()):
        now = _now_local_naive()
        dates = _extract_dates_from_text(text, now)
        if not dates:
            relative = _detect_relative_day(text, now)
            if relative:
                dates = [relative]
        if not dates:
            await update.message.reply_text(t("delete_by_date.invalid", locale=locale))
            return
        count = crud.delete_tasks_by_dates(db, user.id, dates)
        dates_text = _format_date_list(dates)
        await update.message.reply_text(
            t("delete_by_date.done", locale=locale, dates=dates_text, count=count)
        )
        return

    if _looks_like_clear_all(text):
        targets = _parse_clear_targets(text)
        await _prompt_clear_all(targets, update, context, locale=locale)
        return

    suggestion = _detect_suggestion(text)
    if suggestion and not _should_create_task(text):
        context.user_data["pending_suggestion"] = suggestion
        if suggestion["type"] == "followup":
            await update.message.reply_text(
                t("suggestion.followup.ask", locale=locale),
                reply_markup=yes_no_keyboard(locale),
            )
            return
        if suggestion["type"] == "prep":
            await update.message.reply_text(
                t("suggestion.prep.ask", locale=locale),
                reply_markup=yes_no_keyboard(locale),
            )
            return

    lower = text.lower()
    if any(word in lower for word in ["удали", "удалить", "delete", "стереть", "убери задачу"]):
        ids = _extract_task_ids(text)
        if ids:
            await _apply_task_actions("delete", ids, update, db, user)
        else:
            raw_day = context.user_data.get("last_plan_day") if context else None
            day = None
            if raw_day:
                try:
                    day = dt.date.fromisoformat(str(raw_day))
                except ValueError:
                    day = None
            if day is None:
                day = _now_local_naive().date()
            tasks = crud.list_tasks_for_day(db, user.id, day)
            query = _normalize_action_text(text)
            matches = _match_tasks_by_title([t for t in tasks if not t.is_done], query)
            if len(matches) == 1:
                await _apply_task_actions("delete", [matches[0].id], update, db, user)
            elif matches:
                context.user_data["pending_action"] = {
                    "action": "delete",
                    "candidate_ids": [t.id for t in matches],
                }
                lines = [t("tasks.selection.header", locale=locale)]
                lines.extend([_format_task_choice(t, routine, locale) for t in matches])
                lines.append(t("tasks.selection.hint", locale=locale))
                await update.message.reply_text("\n".join(lines))
            else:
                await _prompt_task_selection("delete", update, context, db, user, routine)
        return

    if any(word in lower for word in ["сделано", "готово", "закрыть", "завершить", "выполнено", "выполненной", "done"]):
        ids = _extract_task_ids(text)
        if ids:
            await _apply_task_actions("done", ids, update, db, user)
        else:
            candidates = _resolve_done_candidate(db, user, routine, _now_local_naive())
            if candidates:
                await _apply_task_actions("done", [t.id for t in candidates], update, db, user)
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

    reschedule = _parse_reschedule_request(text, _now_local_naive())
    if reschedule:
        task_id = reschedule["task_id"]
        target_date = reschedule["date"]
        target_time = reschedule["time"]
        duration = reschedule["duration"]
        task = crud.reschedule_task(
            db,
            user.id,
            task_id=task_id,
            target_date=target_date,
            target_time=target_time,
            duration_min=duration,
            schedule_source="manual",
        )
        if not task:
            await update.message.reply_text(t("tasks.reschedule.not_found", locale=locale, task_id=task_id))
            return
        await update.message.reply_text(
            t(
                "tasks.reschedule.success",
                locale=locale,
                task_id=task.id,
                date=target_date.isoformat(),
                start=target_time.strftime("%H:%M"),
            )
        )
        return

    if _is_autoplan_request(text):
        args = _parse_autoplan_args(text)
        await _run_command_with_throttle("autoplan", args, update, context, user)
        return

    if _is_plan_request(text) or _is_tasks_request(text):
        lock = await _acquire_heavy_lock(user, update, text=text, locale=locale)
        if not lock:
            return
        try:
            now = _now_local_naive()
            day = resolve_date_ru(text, now) or now.date()
            ensure_day_anchors(db, user.id, day, routine)
            tasks = crud.list_tasks_for_day(db, user.id, day)
            scheduled = [t for t in tasks if t.planned_start and not t.is_done]
            backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
            _remember_plan_context(context, day, scheduled, backlog)
            await update.message.reply_text(_render_day_plan(scheduled, backlog, day, routine, locale=locale))
        finally:
            lock.release()
        return

    if _is_breakfast_request(text):
        items = crud.list_pantry_items(db, user.id)
        pantry_names = [i.name for i in items]
        suggestions = suggest_meals(pantry_names, meal="breakfast", limit=3)
        if not pantry_names:
            await update.message.reply_text(t("pantry.empty", locale=locale))
            return
        if not suggestions:
            await update.message.reply_text(t("pantry.breakfast.none", locale=locale))
            return
        lines = [t("pantry.breakfast.header", locale=locale)]
        for s in suggestions:
            if s["missing"]:
                missing = ", ".join(s["missing"])
                lines.append(
                    t(
                        "pantry.breakfast.item_missing",
                        locale=locale,
                        name=s["name"],
                        missing=missing,
                    )
                )
            else:
                lines.append(t("pantry.breakfast.item_ready", locale=locale, name=s["name"]))
        await update.message.reply_text("\n".join(lines))
        return

    if _is_backlog_request(text):
        backlog = crud.list_backlog(db, user.id)
        if not backlog:
            await update.message.reply_text(t("backlog.empty", locale=locale))
            return
        lines = [t("backlog.header", locale=locale)]
        for idx, task in enumerate(backlog, start=1):
            minutes = task_display_minutes(task, routine)
            lines.append(
                t(
                    "backlog.line",
                    locale=locale,
                    index=idx,
                    title=task.title,
                    minutes=minutes,
                    task_id=task.id,
                )
            )
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

        await update.message.reply_text(
            t("routine.added.bulk", locale=locale, count=len(items))
        )
        return

    ai_lock = None
    try:
        if ai_enabled:
            guard = check_text_limit(text)
            if not guard.allowed:
                await update.message.reply_text(t(guard.reason, locale=locale))
                return
            guard = check_ai_quota(db, user.id, add_requests=1)
            if not guard.allowed:
                await update.message.reply_text(t(guard.reason, locale=locale))
                return
            guard = _breaker().is_open()
            if not guard.allowed:
                await update.message.reply_text(
                    t(guard.reason, locale=locale, retry_after=guard.retry_after)
                )
                return
            ai_lock = await _acquire_heavy_lock(user, update, text=text, locale=locale)
            if not ai_lock:
                return
            data = parse_intent(text, settings.OPENAI_API_KEY, settings.OPENAI_CHAT_MODEL, locale=locale)
            record_ai_request(db, user.id, count=1)
            if data:
                handled = await _handle_ai_intent(
                    data,
                    text,
                    update,
                    context,
                    db,
                    user,
                    locale=locale,
                )
                if handled:
                    return

        if not _should_create_task(text):
            if ai_enabled:
                guard = check_ai_quota(db, user.id, add_requests=1)
                if not guard.allowed:
                    await update.message.reply_text(t(guard.reason, locale=locale))
                    return
                guard = _breaker().is_open()
                if not guard.allowed:
                    await update.message.reply_text(
                        t(guard.reason, locale=locale, retry_after=guard.retry_after)
                    )
                    return
                if not ai_lock:
                    ai_lock = await _acquire_heavy_lock(user, update, text=text, locale=locale)
                    if not ai_lock:
                        return
                context_prompt = _build_assistant_context(db, user)
                history = _get_chat_history(context)
                reply = chat_reply(
                    text,
                    settings.OPENAI_API_KEY,
                    settings.OPENAI_CHAT_MODEL,
                    system_prompt=_assistant_system_prompt(locale),
                    context_prompt=context_prompt,
                    history=history,
                )
                reply = _sanitize_ai_reply(reply, locale)
                record_ai_request(db, user.id, count=1)
                if reply:
                    _append_chat_history(context, "user", text)
                    _append_chat_history(context, "assistant", reply)
                    await update.message.reply_text(reply)
                    return
                await update.message.reply_text(t("ai.chat.failed", locale=locale))
                return
            await update.message.reply_text(t("ai.hint.create_task", locale=locale))
            return
    finally:
        if ai_lock:
            ai_lock.release()

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
        locale = locale_for_user(user)

        voice = update.message.voice
        guard = check_audio_limits(voice.duration, voice.file_size)
        if not guard.allowed:
            await update.message.reply_text(t(guard.reason, locale=locale))
            return
        guard = check_transcribe_quota(db, user.id, add_seconds=int(voice.duration or 0))
        if not guard.allowed:
            await update.message.reply_text(t(guard.reason, locale=locale))
            return
        guard = _breaker().is_open()
        if not guard.allowed:
            await update.message.reply_text(
                t(guard.reason, locale=locale, retry_after=guard.retry_after)
            )
            return

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
            await update.message.reply_text(t("ai.transcribe.disabled", locale=locale))
            return

        record_transcribe_seconds(db, user.id, seconds=int(voice.duration or 0))
        await update.message.reply_text(t("ai.transcribe.heard", locale=locale, text=transcript))
        if await _handle_onboarding_text(transcript, update, context, db, user):
            return
        await _process_user_text(transcript, update, context, db, user)
