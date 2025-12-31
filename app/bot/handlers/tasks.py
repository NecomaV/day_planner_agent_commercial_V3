from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user, get_user
from app.bot.parsing.commands import parse_yes_no
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
from app.bot.utils import now_local_naive
from app.bot.handlers.routine import start_onboarding
from app.schemas.tasks import TaskCreate
from app.services.autoplan import autoplan_days, ensure_day_anchors
from app.services.quick_capture import parse_quick_task
from app.services.slots import build_busy_intervals, day_bounds, format_gap_options, gaps_from_busy, normalize_date_str, task_display_minutes


def _idempotency_key(update: Update) -> Optional[str]:
    if not update.message or not update.effective_chat:
        return None
    return f"tg:{update.effective_chat.id}:{update.message.message_id}"

def _format_date_list(dates: list[dt.date]) -> str:
    return ", ".join(sorted({d.isoformat() for d in dates}))

def _format_task_choice(task, routine) -> str:
    if task.planned_start:
        when = task.planned_start.strftime("%H:%M")
    elif task.due_at:
        when = task.due_at.strftime("%Y-%m-%d %H:%M")
    else:
        when = "без времени"
    mins = task_display_minutes(task, routine)
    return f"- id={task.id} {task.title} ({when}, ~{mins}м)"

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
    if re.search(r"\b1\b", lower) or "замен" in lower or "replace" in lower:
        return "replace"
    if re.search(r"\b2\b", lower) or "перен" in lower or "move" in lower:
        return "move"
    if re.search(r"\b3\b", lower) or "сдвиг" in lower or "встав" in lower or "shift" in lower or "insert" in lower:
        return "shift"
    if any(word in lower for word in ["отмена", "cancel", "нет", "не надо", "не нужно", "стоп", "stop"]):
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
    suggestion = _suggest_slot_for_task(db, user.id, routine, task)
    if not suggestion:
        await update.message.reply_text("Задача добавлена в бэклог. Свободных слотов не найдено.")
        return
    day, start, end = suggestion
    context.user_data["pending_schedule"] = {
        "task_id": task.id,
        "start": start,
        "end": end,
        "day": day,
    }
    await update.message.reply_text(schedule_offer(day, start, end))

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
    ids = extract_task_ids(text)
    if not ids:
        await update.message.reply_text("Пришлите id задачи или напишите «отмена».")
        return True
    candidate_ids = set(pending.get("candidate_ids") or [])
    if candidate_ids and any(task_id not in candidate_ids for task_id in ids):
        await update.message.reply_text("Пожалуйста, выберите id из списка.")
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
    answer = parse_yes_no(text)
    if answer is None:
        await update.message.reply_text("Ответьте «да» или «нет».")
        return True
    context.user_data.pop("pending_schedule", None)
    if not answer:
        await update.message.reply_text("Ок, оставил в бэклоге.")
        return True
    task_id = pending.get("task_id")
    start = pending.get("start")
    end = pending.get("end")
    if not task_id or not start or not end:
        await update.message.reply_text("Не удалось поставить задачу.")
        return True
    crud.update_task_fields(db, user.id, task_id, planned_start=start, planned_end=end, schedule_source="assistant")
    await update.message.reply_text(f"Запланировано (id={task_id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
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
) -> None:
    await update.message.reply_text(conflict_prompt(conflicts))
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

    now = now_local_naive()
    choice = _parse_conflict_choice(text)
    date_hint, time_range, time_value, duration_hint = _extract_task_timing(text, now)
    if choice is None and (date_hint or time_range or time_value):
        choice = "move"

    if choice is None:
        await update.message.reply_text("Выберите: 1) заменить, 2) перенести, 3) вставить со сдвигом.")
        return True

    if choice == "cancel":
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text("Окей, ничего не делаю.")
        return True

    start = pending.get("start")
    end = pending.get("end")
    title = pending.get("title") or "Задача"
    estimate = int(pending.get("estimate") or 30)
    idempotency_key = pending.get("idempotency_key")
    if not start or not end:
        context.user_data.pop("pending_conflict", None)
        await update.message.reply_text("Не нашел исходное время задачи, попробуйте еще раз.")
        return True

    day = start.date()
    if choice == "replace":
        conflicts = _find_conflicts(db, user.id, start, end)
        blocked = [t for t in conflicts if t.task_type != "user"]
        if blocked:
            await update.message.reply_text(
                "В это время есть системные блоки или рутина, заменить нельзя. Выберите перенос или сдвиг."
            )
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
        deleted_text = f"Заменил: {', '.join(str(i) for i in deleted)}. " if deleted else ""
        await update.message.reply_text(
            f"{deleted_text}Запланировано (id={task.id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()})"
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
                await update.message.reply_text("Не нашел свободного слота, выберите другое время.")
                return True
            duration_minutes = estimate

        if new_end <= new_start:
            await update.message.reply_text("Проверьте время: конец должен быть позже начала.")
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
            await update.message.reply_text(conflict_prompt(conflicts))
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
            f"Запланировано (id={task.id}) {new_start.strftime('%H:%M')}-{new_end.strftime('%H:%M')} ({new_start.date().isoformat()})"
        )
        return True

    if choice == "shift":
        moved, err = _plan_shifted_tasks(db, user.id, day, routine, start, end)
        if err == "blocked":
            await update.message.reply_text(
                "В это время есть системные блоки или рутина, сдвиг невозможен. Выберите перенос."
            )
            return True
        if err == "no_space":
            await update.message.reply_text("Не хватает места в этом дне для сдвига. Выберите перенос.")
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
            f"Запланировано (id={task.id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({day.isoformat()}). "
            f"Сдвинул задач: {len(moved)}."
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
    step = pending.get("step")
    if step == "time":
        if _is_no_due(text):
            payload = TaskCreate(
                title=pending.get("title") or "Задача",
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
            await update.message.reply_text(f"Добавлено в бэклог (id={task.id}).")
            return True

        time_value = _parse_time_value(text)
        if not time_value:
            await update.message.reply_text("Не понял время. Пример: 12:30 или 9 утра.")
            return True

        date = pending.get("date")
        if not isinstance(date, dt.date):
            await update.message.reply_text("Не удалось определить дату задачи.")
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
                title=pending.get("title") or "??????",
                start=start,
                end=end,
                estimate=duration,
            )
            context.user_data.pop("pending_task", None)
            return True

        payload = TaskCreate(
            title=pending.get("title") or "Задача",
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
            f"Запланировано (id={task.id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({date.isoformat()})"
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
        await update.message.reply_text("Не понял дедлайн. Пример: завтра в 15:00 или <без срока>.")
        return True

    payload = TaskCreate(
        title=pending.get("title") or "Задача",
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
    await update.message.reply_text("Задача создана. Ищу слот...")
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
            f"Запланировано (id={task.id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({date.isoformat()})"
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
            f"Запланировано (id={task.id}) {start.strftime('%H:%M')}-{end.strftime('%H:%M')} ({start.date().isoformat()})"
        )
        return True

    if date and not _has_due_intent(text):
        context.user_data["pending_task"] = {
            "step": "time",
            "title": title,
            "date": date,
            "estimate": estimate,
        }
        await update.message.reply_text("Во сколько поставить задачу?")
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
        await update.message.reply_text(f"Добавлено: {task.title}. Срок: {due_at.strftime('%Y-%m-%d %H:%M')}.")
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
    await update.message.reply_text(f"Добавлено в бэклог: {task.title} (id={task.id}).")
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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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

async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /capture <текст>")
        return

    text = " ".join(context.args).strip()
    now = now_local_naive()
    parsed = parse_quick_task(text, now)

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_arg = context.args[0] if context.args else None
    if date_arg:
        try:
            day = normalize_date_str(date_arg)
        except ValueError:
            await update.message.reply_text("Дата должна быть в формате YYYY-MM-DD")
            return
    else:
        day = now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        routine = crud.get_routine(db, user.id)

        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        scheduled = [t for t in tasks if t.planned_start and not t.is_done]
        backlog = [t for t in tasks if t.planned_start is None and not t.is_done and t.task_type == "user"]

        await update.message.reply_text(render_day_plan(scheduled, backlog, day, routine))

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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        routine = crud.get_routine(db, user.id)
        result = autoplan_days(db, user.id, routine, days=days, start_date=start_date)

    suffix = f" {start_date.isoformat()}" if start_date else ""
    await update.message.reply_text(f"Автопланирование завершено: {result}\nПлан: /plan{suffix}")

async def cmd_delay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /delay <id> <минуты>")
        return
    try:
        task_id = int(context.args[0])
        minutes = int(context.args[1])
    except ValueError:
        await update.message.reply_text("id и минуты должны быть числами")
        return
    if minutes <= 0:
        await update.message.reply_text("Минуты должны быть > 0")
        return
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        task = crud.get_task(db, user.id, task_id)
        if not task:
            await update.message.reply_text("Задача не найдена")
            return
        if not task.planned_start or not task.planned_end:
            await update.message.reply_text("У задачи нет времени, нечего сдвигать.")
            return
        delta = dt.timedelta(minutes=minutes)
        new_start = task.planned_start + delta
        new_end = task.planned_end + delta
        crud.update_task_fields(db, user.id, task_id, planned_start=new_start, planned_end=new_end, schedule_source="assistant")
        await update.message.reply_text(
            f"Перенесено (id={task_id}) {new_start.strftime('%H:%M')}-{new_end.strftime('%H:%M')}"
        )

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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
            day = task.planned_start.date() if task.planned_start else now_local_naive().date()

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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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

        day = task.planned_start.date() if task.planned_start else now_local_naive().date()

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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
            day = task.planned_start.date() if task.planned_start else now_local_naive().date()

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

async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /unschedule <id> [id2 ...]")
        return

    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        await _apply_task_actions("unschedule", ids, update, db, user)

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /done <id> [id2 ...]")
        return
    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        await _apply_task_actions("done", ids, update, db, user)

async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /delete <id> [id2 ...]")
        return

    ids = extract_task_ids(" ".join(context.args))
    if not ids:
        await update.message.reply_text("id должен быть числом")
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
