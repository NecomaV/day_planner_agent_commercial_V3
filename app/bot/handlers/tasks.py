from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.parsing.ru_reply import parse_reply
from app.bot.parsing.text import extract_task_ids, is_no_due
from app.bot.parsing.time import (
    _extract_task_timing,
    _parse_time_range,
    _parse_time_value,
    _resolve_date_for_time,
)
from app.bot.parsing.tasks import normalize_task_title, shorten_title
from app.bot.parsing.values import parse_int_value
from app.bot.rendering.tasks import conflict_prompt, render_day_plan, schedule_offer
from app.bot.rendering.keyboard import yes_no_keyboard, yes_no_cancel_keyboard
from app.bot.utils import now_local_naive
from app.bot.handlers.routine import start_onboarding
from app.i18n.core import locale_for_user, t
from app.schemas.tasks import TaskCreate
from app.services.autoplan import autoplan_days, ensure_day_anchors
from app.services.quick_capture import parse_quick_task
from app.services.slots import (
    Interval,
    build_busy_intervals,
    day_bounds,
    format_gap_options,
    gaps_from_busy,
    normalize_date_str,
    parse_hhmm,
    task_display_minutes,
)
from app.settings import settings


def _idempotency_key(update: Update) -> Optional[str]:
    if not update.message or not update.effective_chat:
        return None
    return f"tg:{update.effective_chat.id}:{update.message.message_id}"

def _format_date_list(dates: list[dt.date]) -> str:
    return ", ".join(sorted({d.isoformat() for d in dates}))

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


def _list_open_tasks(db, user, day: dt.date):
    tasks = crud.list_tasks_for_day(db, user.id, day)
    return [t for t in tasks if not t.is_done]

def _find_conflicts(db, user_id: int, start: dt.datetime, end: dt.datetime) -> list:
    day = start.date()
    tasks = crud.list_tasks_for_day(db, user_id, day)
    conflicts = []
    for task in tasks:
        if not task.planned_start or not task.planned_end:
            continue
        if task.is_done:
            continue
        if task.planned_start < end and task.planned_end > start:
            conflicts.append(task)
    return conflicts
def _parse_conflict_choice(text: str) -> str | None:
    lower = text.strip().lower()
    if re.search(r"\b1\b", lower) or any(word in lower for word in ["замени", "заменить", "replace"]):
        return "replace"
    if re.search(r"\b2\b", lower) or any(word in lower for word in ["перенеси", "перенести", "move"]):
        return "move"
    if re.search(r"\b3\b", lower) or any(word in lower for word in ["сдвинь", "сдвинуть", "вставь", "вставить", "shift", "insert"]):
        return "shift"
    flags = parse_reply(text)
    if flags.is_cancel or flags.is_no:
        return "cancel"
    return None

def _find_next_gap_after(
    db,
    user_id: int,
    day: dt.date,
    routine,
    duration: dt.timedelta,
    after: dt.datetime,
) -> tuple[dt.datetime, dt.datetime] | None:
    gaps, day_start, _ = _gaps_for_day(db, user_id, day, routine)
    cursor = max(after, day_start)
    for gap in gaps:
        if gap.end <= cursor:
            continue
        start = max(gap.start, cursor)
        if start + duration <= gap.end:
            return start, start + duration
    return None

def _plan_shifted_tasks(
    db,
    user_id: int,
    day: dt.date,
    routine,
    start: dt.datetime,
    end: dt.datetime,
) -> tuple[list[tuple[object, dt.datetime, dt.datetime]], str | None]:
    tasks = crud.list_tasks_for_day(db, user_id, day)
    scheduled = [t for t in tasks if t.planned_start and t.planned_end and not t.is_done]
    fixed = [t for t in scheduled if t.task_type != "user" or t.planned_end <= start]
    blockers = [t for t in fixed if t.planned_start < end and t.planned_end > start]
    if blockers:
        return [], "blocked"

    movable = [t for t in scheduled if t.task_type == "user" and t.planned_end > start]
    movable.sort(key=lambda t: t.planned_start)

    now = now_local_naive()
    day_start, day_end, _morn_s, _morn_e = day_bounds(day, routine, now=now)
    busy = build_busy_intervals(fixed, routine)
    busy.append(Interval(start, end))
    buffer_after = dt.timedelta(minutes=int(getattr(routine, "task_buffer_after_min", 0) or 0))

    moved: list[tuple[object, dt.datetime, dt.datetime]] = []
    cursor = end
    for task in movable:
        duration = task.planned_end - task.planned_start
        gaps = gaps_from_busy(busy, day_start, day_end)
        slot = None
        for gap in gaps:
            if gap.end <= cursor:
                continue
            candidate = max(gap.start, cursor, task.planned_start)
            if candidate + duration <= gap.end:
                slot = (candidate, candidate + duration)
                break
        if not slot:
            return [], "no_space"
        new_start, new_end = slot
        moved.append((task, new_start, new_end))
        busy.append(Interval(new_start, new_end + buffer_after))
        cursor = new_end + buffer_after

    if moved and cursor > day_end:
        return [], "no_space"

    return moved, None

def _suggest_slot_for_task(db, user_id: int, routine, task) -> tuple[dt.date, dt.datetime, dt.datetime] | None:
    now = now_local_naive()
    duration = dt.timedelta(minutes=task_display_minutes(task, routine))
    for offset in range(0, 3):
        day = now.date() + dt.timedelta(days=offset)
        gaps, _, _ = _gaps_for_day(db, user_id, day, routine)
        for gap in gaps:
            if gap.end - gap.start >= duration:
                start = gap.start
                end = start + duration
                return day, start, end
    return None

async def _offer_schedule(
    task,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    routine,
) -> None:
    locale = locale_for_user(user)
    suggestion = _suggest_slot_for_task(db, user.id, routine, task)
    if not suggestion:
        await update.message.reply_text(t("tasks.schedule.none", locale=locale))
        return
    day, start, end = suggestion
    context.user_data["pending_schedule"] = {
        "task_id": task.id,
        "start": start,
        "end": end,
        "day": day,
    }
    await update.message.reply_text(
        schedule_offer(day, start, end, locale=locale),
        reply_markup=yes_no_keyboard(locale),
    )

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
    locale = locale_for_user(user)
    if deleted:
        parts.append(
            t("tasks.action.deleted", locale=locale, ids=", ".join(str(i) for i in deleted))
        )
    if done:
        parts.append(t("tasks.action.done", locale=locale, ids=", ".join(str(i) for i in done)))
    if unscheduled:
        parts.append(
            t("tasks.action.unscheduled", locale=locale, ids=", ".join(str(i) for i in unscheduled))
        )
    if skipped:
        parts.append(
            t("tasks.action.skipped", locale=locale, ids=", ".join(str(i) for i in skipped))
        )

    if not parts:
        await update.message.reply_text(t("tasks.action.none", locale=locale))
        return False
    await update.message.reply_text("\n".join(parts))
    return True

async def _prompt_task_selection(action: str, update: Update, context: ContextTypes.DEFAULT_TYPE, db, user, routine) -> None:
    day = None
    raw_day = context.user_data.get("last_plan_day") if context else None
    if raw_day:
        try:
            day = dt.date.fromisoformat(str(raw_day))
        except ValueError:
            day = None
    if day is None:
        day = now_local_naive().date()
    tasks = _list_open_tasks(db, user, day)
    locale = locale_for_user(user)
    if not tasks:
        await update.message.reply_text(t("tasks.selection.empty", locale=locale))
        return
    context.user_data["pending_action"] = {
        "action": action,
        "candidate_ids": [t.id for t in tasks],
    }
    lines = [t("tasks.selection.header", locale=locale)]
    lines.extend([_format_task_choice(t, routine, locale) for t in tasks])
    lines.append(t("tasks.selection.hint", locale=locale))
    await update.message.reply_text("\n".join(lines))

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
    locale = locale_for_user(user)
    lower = text.strip().lower()
    if lower.startswith("/"):
        context.user_data.pop("pending_action", None)
        return False
    if re.search(r"\b(план|расписание|график|бэклог|беклог|backlog)\b", lower):
        context.user_data.pop("pending_action", None)
        return False
    flags = parse_reply(text)
    if flags.is_cancel:
        context.user_data.pop("pending_action", None)
        await update.message.reply_text(t("tasks.selection.cancelled", locale=locale))
        return True
    ids = extract_task_ids(text)
    if not ids:
        await update.message.reply_text(t("tasks.selection.invalid", locale=locale))
        return True
    candidate_ids = set(pending.get("candidate_ids") or [])
    if candidate_ids and any(task_id not in candidate_ids for task_id in ids):
        await update.message.reply_text(t("tasks.selection.out_of_range", locale=locale))
        return True
    context.user_data.pop("pending_action", None)
    return await _apply_task_actions(pending.get("action", ""), ids, update, db, user)

async def _handle_pending_schedule(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
) -> bool:
    pending = context.user_data.get("pending_schedule")
    if not pending:
        return False
    locale = locale_for_user(user)
    flags = parse_reply(text)
    if flags.is_cancel:
        context.user_data.pop("pending_schedule", None)
        await update.message.reply_text(t("tasks.schedule.cancelled", locale=locale))
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
    context.user_data.pop("pending_schedule", None)
    if not answer:
        await update.message.reply_text(t("tasks.schedule.declined", locale=locale))
        return True
    task_id = pending.get("task_id")
    start = pending.get("start")
    end = pending.get("end")
    if not task_id or not start or not end:
        await update.message.reply_text(t("tasks.schedule.failed", locale=locale))
        return True
    crud.update_task_fields(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="assistant")
    await update.message.reply_text(
        t(
            "tasks.schedule.success",
            locale=locale,
            task_id=task_id,
            start=start.strftime("%H:%M"),
            end=end.strftime("%H:%M"),
        )
    )
    return True

async def _prompt_conflict_resolution(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    conflicts: list,
    *,
    title: str,
    start: dt.datetime,
    end: dt.datetime,
    estimate: int,
    idempotency_key: str | None = None,
    locale: str,
) -> None:
    await update.message.reply_text(conflict_prompt(conflicts, locale=locale))
    context.user_data["pending_conflict"] = {
        "title": title,
        "start": start,
        "end": end,
        "estimate": estimate,
        "idempotency_key": idempotency_key,
    }

async def _handle_pending_conflict(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    routine,
) -> bool:
    pending = context.user_data.get("pending_conflict")
    if not pending:
        return False
    locale = locale_for_user(user)

    now = now_local_naive()
    choice = _parse_conflict_choice(text)
    date_hint, time_range, time_value, duration_hint = _extract_task_timing(text, now)
    if choice is None and (date_hint or time_range or time_value):
        choice = "move"

    if choice is None:
        await update.message.reply_text(t("tasks.conflict.choose", locale=locale))
        return True

    if choice == "cancel":
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text(t("tasks.conflict.cancelled", locale=locale))
        return True

    start = pending.get("start")
    end = pending.get("end")
    title = pending.get("title") or t("tasks.default_title", locale=locale)
    estimate = int(pending.get("estimate") or 30)
    idempotency_key = pending.get("idempotency_key")
    if not start or not end:
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text(t("tasks.conflict.missing_time", locale=locale))
        return True

    day = start.date()
    if choice == "replace":
        conflicts = _find_conflicts(db, user.id, start, end)
        blocked = [t for t in conflicts if t.task_type != "user"]
        if blocked:
            await update.message.reply_text(t("tasks.conflict.blocked_replace", locale=locale))
            return True
        deleted = []
        for task in conflicts:
            if task.task_type == "user":
                crud.delete_task(db, user.id, task.id)
                deleted.append(task.id)
        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=start,
            planned_end=end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        context.user_data.pop("pending_conflict", None)
        deleted_text = ", ".join(str(i) for i in deleted) if deleted else ""
        await update.message.reply_text(
            t(
                "tasks.conflict.replaced",
                locale=locale,
                deleted=deleted_text,
                task_id=task.id,
                start=start.strftime("%H:%M"),
                end=end.strftime("%H:%M"),
                date=day.isoformat(),
            )
        )
        return True

    if choice == "move":
        if time_range:
            base_date = date_hint or day
            new_start = dt.datetime.combine(base_date, time_range[0])
            new_end = dt.datetime.combine(base_date, time_range[1])
            duration_minutes = max(1, int((new_end - new_start).total_seconds() // 60))
        elif time_value:
            base_date = date_hint or day
            duration_minutes = int(duration_hint or estimate)
            new_start = dt.datetime.combine(base_date, time_value)
            new_end = new_start + dt.timedelta(minutes=duration_minutes)
        else:
            base_date = date_hint or day
            duration_td = dt.timedelta(minutes=estimate)
            new_start = new_end = None
            for offset in range(0, 3):
                candidate_day = base_date + dt.timedelta(days=offset)
                after = start if candidate_day == day and offset == 0 else dt.datetime.combine(candidate_day, dt.time.min)
                slot = _find_next_gap_after(db, user.id, candidate_day, routine, duration_td, after)
                if slot:
                    new_start, new_end = slot
                    break
            if not new_start or not new_end:
                await update.message.reply_text(t("tasks.conflict.no_slot", locale=locale))
                return True
            duration_minutes = estimate

        if new_end <= new_start:
            await update.message.reply_text(t("tasks.conflict.invalid_range", locale=locale))
            return True

        conflicts = _find_conflicts(db, user.id, new_start, new_end)
        if conflicts:
            context.user_data["pending_conflict"] = {
                "title": title,
                "start": new_start,
                "end": new_end,
                "estimate": duration_minutes,
                "idempotency_key": idempotency_key,
            }
            await update.message.reply_text(conflict_prompt(conflicts, locale=locale))
            return True

        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=duration_minutes,
            planned_start=new_start,
            planned_end=new_end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text(
            t(
                "tasks.conflict.moved",
                locale=locale,
                task_id=task.id,
                start=new_start.strftime("%H:%M"),
                end=new_end.strftime("%H:%M"),
                date=new_start.date().isoformat(),
            )
        )
        return True

    if choice == "shift":
        moved, err = _plan_shifted_tasks(db, user.id, day, routine, start, end)
        if err == "blocked":
            await update.message.reply_text(t("tasks.conflict.blocked_shift", locale=locale))
            return True
        if err == "no_space":
            await update.message.reply_text(t("tasks.conflict.no_space", locale=locale))
            return True

        for task, new_start, new_end in moved:
            crud.update_task_fields(
                db,
                user.id,
                task.id,
                planned_start=new_start,
                planned_end=new_end,
                schedule_source="assistant",
            )

        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=start,
            planned_end=end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text(
            t(
                "tasks.conflict.shifted",
                locale=locale,
                task_id=task.id,
                start=start.strftime("%H:%M"),
                end=end.strftime("%H:%M"),
                date=day.isoformat(),
                moved=len(moved),
            )
        )
        return True

    return True

async def _handle_pending_task(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    routine,
) -> bool:
    pending = context.user_data.get("pending_task")
    if not pending:
        return False
    locale = locale_for_user(user)
    step = pending.get("step")
    if step == "time":
        if _is_no_due(text):
            payload = TaskCreate(
                title=pending.get("title") or t("tasks.default_title", locale=locale),
                notes=None,
                estimate_minutes=int(pending.get("estimate") or 30),
                planned_start=None,
                planned_end=None,
                due_at=None,
                priority=2,
                kind=None,
                idempotency_key=None,
            )
            task = crud.create_task(db, user_id=user.id, data=payload)
            context.user_data.pop("pending_task", None)
            await update.message.reply_text(
                t("tasks.pending.backlog_added", locale=locale, task_id=task.id)
            )
            return True

        time_value = _parse_time_value(text)
        if not time_value:
            await update.message.reply_text(t("tasks.pending.time_invalid", locale=locale))
            return True

        date = pending.get("date")
        if not isinstance(date, dt.date):
            await update.message.reply_text(t("tasks.pending.date_missing", locale=locale))
            context.user_data.pop("pending_task", None)
            return True

        start = dt.datetime.combine(date, time_value)
        duration = int(pending.get("estimate") or 30)
        end = start + dt.timedelta(minutes=duration)
        conflicts = _find_conflicts(db, user.id, start, end)
        if conflicts:
            await _prompt_conflict_resolution(
                update,
                context,
                conflicts,
                title=pending.get("title") or t("tasks.default_title", locale=locale),
                start=start,
                end=end,
                estimate=duration,
                locale=locale,
            )
            context.user_data.pop("pending_task", None)
            return True

        payload = TaskCreate(
            title=pending.get("title") or t("tasks.default_title", locale=locale),
            notes=None,
            estimate_minutes=duration,
            planned_start=start,
            planned_end=end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=None,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        context.user_data.pop("pending_task", None)
        await update.message.reply_text(
            t(
                "tasks.pending.scheduled",
                locale=locale,
                task_id=task.id,
                start=start.strftime("%H:%M"),
                end=end.strftime("%H:%M"),
                date=date.isoformat(),
            )
        )
        return True

    if step != "due":
        return False

    now = now_local_naive()
    if _is_no_due(text):
        due_at = None
    else:
        parsed = parse_quick_task(text, now)
        due_at = parsed.due_at
        if parsed.title and parsed.title != text:
            pending["title"] = pending.get("title") or parsed.title
        if due_at is None:
            date, time_range, time_value, _ = _extract_task_timing(text, now)
            if date and time_range:
                due_at = dt.datetime.combine(date, time_range[1])
            elif date and time_value:
                due_at = dt.datetime.combine(date, time_value)
            elif date:
                due_at = dt.datetime.combine(date, dt.time(18, 0))

    if not _is_no_due(text) and due_at is None:
        await update.message.reply_text(t("tasks.pending.due_invalid", locale=locale))
        return True

    payload = TaskCreate(
        title=pending.get("title") or t("tasks.default_title", locale=locale),
        notes=None,
        estimate_minutes=int(pending.get("estimate") or 30),
        planned_start=None,
        planned_end=None,
        due_at=due_at,
        priority=2,
        kind=None,
        idempotency_key=None,
    )
    task = crud.create_task(db, user_id=user.id, data=payload)
    context.user_data.pop("pending_task", None)
    await update.message.reply_text(t("tasks.pending.created_searching", locale=locale))
    await _offer_schedule(task, update, context, db, user, routine)
    return True

async def _handle_task_request(
    text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user,
    routine,
    *,
    schedule_source: str = "assistant",
    idempotency_key: str | None = None,
) -> bool:
    now = now_local_naive()
    parsed = parse_quick_task(text, now)
    locale = locale_for_user(user)
    title = parsed.title.strip() if parsed.title else text.strip()
    title = normalize_task_title(title)
    title = shorten_title(title)
    date, time_range, time_value, duration = _extract_task_timing(text, now)
    estimate = duration or 30

    if date and time_range:
        start = dt.datetime.combine(date, time_range[0])
        end = dt.datetime.combine(date, time_range[1])
        conflicts = _find_conflicts(db, user.id, start, end)
        if conflicts:
            await _prompt_conflict_resolution(
                update,
                context,
                conflicts,
                title=title,
                start=start,
                end=end,
                estimate=estimate,
                idempotency_key=idempotency_key,
                locale=locale,
            )
            return True

        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=start,
            planned_end=end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        await update.message.reply_text(
            t(
                "tasks.request.scheduled",
                locale=locale_for_user(user),
                task_id=task.id,
                start=start.strftime("%H:%M"),
                end=end.strftime("%H:%M"),
                date=date.isoformat(),
            )
        )
        return True

    if time_value:
        start = _resolve_date_for_time(now, date, time_value)
        end = start + dt.timedelta(minutes=estimate)
        conflicts = _find_conflicts(db, user.id, start, end)
        if conflicts:
            await _prompt_conflict_resolution(
                update,
                context,
                conflicts,
                title=title,
                start=start,
                end=end,
                estimate=estimate,
                idempotency_key=idempotency_key,
                locale=locale,
            )
            return True

        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=start,
            planned_end=end,
            due_at=None,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        await update.message.reply_text(
            t(
                "tasks.request.scheduled",
                locale=locale_for_user(user),
                task_id=task.id,
                start=start.strftime("%H:%M"),
                end=end.strftime("%H:%M"),
                date=start.date().isoformat(),
            )
        )
        return True

    if date and not _has_due_intent(text):
        context.user_data["pending_task"] = {
            "step": "time",
            "title": title,
            "date": date,
            "estimate": estimate,
        }
        await update.message.reply_text(
            t("tasks.request.ask_time", locale=locale_for_user(user))
        )
        return True

    if date and _has_due_intent(text):
        due_at = dt.datetime.combine(date, dt.time(18, 0))
        payload = TaskCreate(
            title=title,
            notes=None,
            estimate_minutes=estimate,
            planned_start=None,
            planned_end=None,
            due_at=due_at,
            priority=2,
            kind=None,
            idempotency_key=idempotency_key,
        )
        task = crud.create_task(db, user_id=user.id, data=payload)
        await update.message.reply_text(
            t(
                "tasks.request.added_with_due",
                locale=locale_for_user(user),
                title=task.title,
                due_at=due_at.strftime("%Y-%m-%d %H:%M"),
            )
        )
        await _offer_schedule(task, update, context, db, user, routine)
        return True

    payload = TaskCreate(
        title=title,
        notes=None,
        estimate_minutes=estimate,
        planned_start=None,
        planned_end=None,
        due_at=None,
        priority=2,
        kind=None,
        idempotency_key=idempotency_key,
    )
    task = crud.create_task(db, user_id=user.id, data=payload)
    if parsed.checklist_items:
        crud.add_checklist_items(db, task.id, parsed.checklist_items)
    await update.message.reply_text(
        t(
            "tasks.request.backlog_added",
            locale=locale_for_user(user),
            title=task.title,
            task_id=task.id,
        )
    )
    return True

def _gaps_for_day(db, user_id: int, day: dt.date, routine):
    ensure_day_anchors(db, user_id, day, routine)

    all_tasks = crud.list_tasks_for_day(db, user_id, day)
    scheduled = [t for t in all_tasks if t.planned_start and not t.is_done]

    now = now_local_naive()
    day_start, day_end, _morn_s, _morn_e = day_bounds(day, routine, now=now)

    busy = build_busy_intervals(scheduled, routine)
    gaps = gaps_from_busy(busy, day_start, day_end)
    return gaps, day_start, day_end

async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(t("tasks.todo.usage", locale="ru"))
        return

    try:
        estimate = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("tasks.todo.minutes_invalid", locale="ru"))
        return

    title = " ".join(context.args[1:]).strip()
    if not title:
        await update.message.reply_text(t("tasks.todo.title_empty", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
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
        await update.message.reply_text(
            t("tasks.todo.created", locale=locale, task_id=task.id)
        )

async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.capture.usage", locale="ru"))
        return

    text = " ".join(context.args).strip()
    now = now_local_naive()
    parsed = parse_quick_task(text, now)

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
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

    when = parsed.due_at.strftime("%Y-%m-%d %H:%M") if parsed.due_at else t("tasks.capture.no_due", locale=locale)
    checklist_info = (
        t("tasks.capture.checklist_info", locale=locale, count=len(parsed.checklist_items))
        if parsed.checklist_items
        else ""
    )
    await update.message.reply_text(
        t("tasks.capture.added", locale=locale, title=task.title, when=when, checklist=checklist_info)
    )


async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("call.usage", locale="ru"))
        return

    text = " ".join(context.args).strip()
    now = now_local_naive()
    parsed = parse_quick_task(text, now)

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        name = parsed.title.strip() if parsed.title else t("suggestion.followup.default_name", locale=locale)
        due_at = parsed.due_at
        if not due_at:
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
            idempotency_key=_idempotency_key(update),
        )
        crud.add_checklist_items(
            db,
            task.id,
            [t("suggestion.followup.checklist", locale=locale, name=name)],
        )

        due_text = due_at.strftime("%Y-%m-%d %H:%M")
        await update.message.reply_text(
            t("call.created", locale=locale, task_id=task.id, due=due_text)
        )

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_arg = context.args[0] if context.args else None
    if date_arg:
        try:
            day = normalize_date_str(date_arg)
        except ValueError:
            await update.message.reply_text(t("tasks.plan.invalid_date", locale="ru"))
            return
    else:
        day = now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        routine = crud.get_routine(db, user.id)

        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]
        context.user_data["last_plan_day"] = day.isoformat()
        context.user_data["last_plan_task_ids"] = [t.id for t in scheduled] + [t.id for t in backlog]

        await update.message.reply_text(render_day_plan(scheduled, backlog, day, routine, locale=locale))

async def cmd_autoplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.autoplan.usage", locale="ru"))
        return

    try:
        days = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("tasks.autoplan.days_invalid", locale="ru"))
        return

    start_date = None
    if len(context.args) >= 2:
        try:
            start_date = normalize_date_str(context.args[1])
        except ValueError:
            await update.message.reply_text(t("tasks.plan.invalid_date", locale="ru"))
            return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        locale = locale_for_user(user)
        result = autoplan_days(db, user.id, routine, days=days, start_date=start_date)

    suffix = f" {start_date.isoformat()}" if start_date else ""
    await update.message.reply_text(
        t("tasks.autoplan.done", locale=locale, result=result, suffix=suffix)
    )

async def cmd_delay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(t("tasks.delay.usage", locale="ru"))
        return
    try:
        task_id = int(context.args[0])
        minutes = int(context.args[1])
    except ValueError:
        await update.message.reply_text(t("tasks.delay.invalid_numbers", locale="ru"))
        return
    if minutes <= 0:
        await update.message.reply_text(t("tasks.delay.minutes_invalid", locale="ru"))
        return
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text(t("tasks.delay.not_found", locale=locale))
            return
        if not task.planned_start or not task.planned_end:
            await update.message.reply_text(t("tasks.delay.no_time", locale=locale))
            return
        delta = dt.timedelta(minutes=minutes)
        new_start = task.planned_start + delta
        new_end = task.planned_end + delta
        crud.update_task_fields(db, user.id, task_id, planned_start=new_start, planned_end=new_end, schedule_source="assistant")
        await update.message.reply_text(
            t(
                "tasks.delay.success",
                locale=locale,
                task_id=task_id,
                start=new_start.strftime("%H:%M"),
                end=new_end.strftime("%H:%M"),
            )
        )

async def cmd_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.slots.usage", locale="ru"))
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("tasks.slots.id_invalid", locale="ru"))
        return

    date_arg = context.args[1] if len(context.args) >= 2 else None

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text(t("tasks.common.not_found", locale=locale))
            return
        if task.task_type != "user":
            await update.message.reply_text(t("tasks.slots.only_user", locale=locale))
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text(t("tasks.plan.invalid_date", locale=locale))
                return
        else:
            day = task.planned_start.date() if task.planned_start else now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        text = format_gap_options(task, gaps, routine, day)

    await update.message.reply_text(text)

async def cmd_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(t("tasks.place.usage", locale="ru"))
        return

    try:
        task_id = int(context.args[0])
        slot_idx = int(context.args[1])
    except ValueError:
        await update.message.reply_text(t("tasks.place.id_invalid", locale="ru"))
        return

    hhmm = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text(t("tasks.common.not_found", locale=locale))
            return
        if task.task_type != "user":
            await update.message.reply_text(t("tasks.place.only_user", locale=locale))
            return

        day = task.planned_start.date() if task.planned_start else now_local_naive().date()

        gaps, _, _ = _gaps_for_day(db, user.id, day, routine)
        if slot_idx < 1 or slot_idx > len(gaps):
            await update.message.reply_text(t("tasks.place.slot_invalid", locale=locale))
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
            await update.message.reply_text(t("tasks.place.slot_unfit", locale=locale))
            return

        start = earliest
        if hhmm:
            try:
                t = parse_hhmm(hhmm)
            except Exception:
                await update.message.reply_text(t("tasks.place.time_invalid", locale=locale))
                return
            candidate = dt.datetime.combine(day, t)
            if candidate < earliest or candidate > latest:
                await update.message.reply_text(
                    t(
                        "tasks.place.time_out_of_slot",
                        locale=locale,
                        start=earliest.strftime("%H:%M"),
                        end=latest.strftime("%H:%M"),
                    )
                )
                return
            start = candidate

        end = start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(
        t(
            "tasks.place.success",
            locale=locale,
            task_id=task_id,
            start=start.strftime("%H:%M"),
            end=end.strftime("%H:%M"),
            date=day.isoformat(),
        )
    )

async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text(t("tasks.schedule_cmd.usage", locale="ru"))
        return

    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("tasks.schedule_cmd.id_invalid", locale="ru"))
        return

    hhmm = context.args[1]
    date_arg = context.args[2] if len(context.args) >= 3 else None

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        routine = crud.get_routine(db, user.id)
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text(t("tasks.common.not_found", locale=locale))
            return
        if task.task_type != "user":
            await update.message.reply_text(t("tasks.schedule_cmd.only_user", locale=locale))
            return

        if date_arg:
            try:
                day = normalize_date_str(date_arg)
            except ValueError:
                await update.message.reply_text(t("tasks.plan.invalid_date", locale=locale))
                return
        else:
            day = task.planned_start.date() if task.planned_start else now_local_naive().date()

        try:
            t = parse_hhmm(hhmm)
        except Exception:
            await update.message.reply_text(t("tasks.schedule_cmd.time_invalid", locale=locale))
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
            await update.message.reply_text(t("tasks.schedule_cmd.time_unfit", locale=locale))
            return

        end = desired_start + core
        crud.update_task_fields(db, user.id, task_id, planned_start=desired_start, planned_end=end, schedule_source="manual")

    await update.message.reply_text(
        t(
            "tasks.schedule_cmd.success",
            locale=locale,
            task_id=task_id,
            start=desired_start.strftime("%H:%M"),
            end=end.strftime("%H:%M"),
            date=day.isoformat(),
        )
    )

async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.unschedule.usage", locale="ru"))
        return

    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text(t("tasks.common.id_invalid", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        await _apply_task_actions("unschedule", ids, update, db, user)

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.done.usage", locale="ru"))
        return
    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text(t("tasks.common.id_invalid", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        await _apply_task_actions("done", ids, update, db, user)

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("tasks.delete.usage", locale="ru"))
        return

    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text(t("tasks.common.id_invalid", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        await _apply_task_actions("delete", ids, update, db, user)


# Re-export helpers for message handler
apply_task_actions = _apply_task_actions
prompt_task_selection = _prompt_task_selection
handle_pending_action = _handle_pending_action
handle_pending_schedule = _handle_pending_schedule
handle_pending_task = _handle_pending_task
handle_pending_conflict = _handle_pending_conflict
handle_task_request = _handle_task_request
