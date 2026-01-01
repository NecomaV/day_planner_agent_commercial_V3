from __future__ import annotations

import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user, get_user
from app.bot.parsing.commands import parse_yes_no
from app.bot.parsing.ru_reply import parse_reply
from app.bot.parsing.text import is_skip, split_items
from app.bot.parsing.values import parse_int_value
from app.bot.utils import now_local_naive
from app.bot.rendering.keyboard import yes_no_keyboard, yes_no_cancel_keyboard
from app.i18n.core import locale_for_user, t, t_list
from app.services.autoplan import ensure_day_anchors
from app.services.ai_intent import suggest_routine_steps


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["onboarding_step"] = "name"
    await update.message.reply_text(t("onboarding.start", locale="ru"))

def _suggest_routine_steps(goal: str) -> list[str]:
    goal = goal.strip().lower()
    if any(x in goal for x in ["fitness", "gym", "workout", "health"]):
        return ["Стакан воды", "Растяжка", "Легкая тренировка", "Белковый завтрак", "План дня"]
    if any(x in goal for x in ["work", "focus", "business", "study", "learn"]):
        return ["Стакан воды", "Приоритеты дня", "Фокус-блок", "Завтрак", "Разбор входящих"]
    if any(x in goal for x in ["family", "kids", "home"]):
        return ["Стакан воды", "Проверка семейного расписания", "Завтрак", "Сборы", "Быстрая уборка"]
    return ["Стакан воды", "Растяжка", "Завтрак", "План дня"]

async def handle_onboarding_text(
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
        await start_onboarding(update, context)
        return True

    routine = crud.get_routine(db, user.id)
    locale = locale_for_user(user)

    if step == "name":
        if is_skip(text):
            user.full_name = None
        else:
            user.full_name = text.strip()[:120]
        db.add(user)
        db.commit()
        context.user_data["onboarding_step"] = "timezone"
        await update.message.reply_text(t("onboarding.ask_timezone", locale=locale))
        return True

    if step == "timezone":
        if not is_skip(text):
            user.timezone = text.strip()
        db.add(user)
        db.commit()
        context.user_data["onboarding_step"] = "wake"
        await update.message.reply_text(t("onboarding.ask_wake", locale=locale))
        return True

    if step == "wake":
        if is_skip(text):
            context.user_data["onboarding_step"] = "bed"
            await update.message.reply_text(t("onboarding.ask_bed", locale=locale))
            return True
        t = _parse_time_value(text) or None
        if not t:
            await update.message.reply_text(t("onboarding.time_invalid", locale=locale))
            return True
        wake_str = f"{t.hour:02d}:{t.minute:02d}"
        _apply_wake_time(routine, wake_str)
        db.add(routine)
        db.commit()
        context.user_data["onboarding_step"] = "bed"
        await update.message.reply_text(t("onboarding.ask_bed_thanks", locale=locale))
        return True

    if step == "bed":
        if is_skip(text):
            context.user_data["onboarding_step"] = "workday"
            await update.message.reply_text(t("onboarding.ask_workday", locale=locale))
            return True
        t = _parse_time_value(text) or None
        if not t:
            await update.message.reply_text(t("onboarding.time_invalid_bed", locale=locale))
            return True
        bed_str = f"{t.hour:02d}:{t.minute:02d}"
        routine.sleep_target_bedtime = bed_str
        db.add(routine)
        db.commit()
        context.user_data["onboarding_step"] = "workday"
        await update.message.reply_text(t("onboarding.ask_workday", locale=locale))
        return True

    if step == "workday":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if not time_range:
                await update.message.reply_text(t("onboarding.workday_invalid", locale=locale))
                return True
            start, end = time_range
            routine.workday_start = start.strftime("%H:%M")
            routine.workday_end = end.strftime("%H:%M")
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "latest_end"
        await update.message.reply_text(t("onboarding.ask_latest_end", locale=locale))
        return True

    if step == "latest_end":
        if not is_skip(text):
            t = _parse_time_value(text) or None
            if not t:
                await update.message.reply_text(t("onboarding.latest_end_invalid", locale=locale))
                return True
            routine.latest_task_end = f"{t.hour:02d}:{t.minute:02d}"
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "task_buffer"
        await update.message.reply_text(t("onboarding.ask_task_buffer", locale=locale))
        return True

    if step == "task_buffer":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text(t("onboarding.task_buffer_invalid", locale=locale))
                return True
            routine.task_buffer_after_min = max(0, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "lunch"
        await update.message.reply_text(t("onboarding.ask_lunch", locale=locale))
        return True

    if step == "lunch":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if time_range:
                _set_meal_window_range(routine, "lunch", time_range[0], time_range[1])
            else:
                t = _parse_time_value(text) or None
                if not t:
                    await update.message.reply_text(t("onboarding.lunch_invalid", locale=locale))
                    return True
                _set_meal_window(routine, "lunch", t)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "dinner"
        await update.message.reply_text(t("onboarding.ask_dinner", locale=locale))
        return True

    if step == "dinner":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if time_range:
                _set_meal_window_range(routine, "dinner", time_range[0], time_range[1])
            else:
                t = _parse_time_value(text) or None
                if not t:
                    await update.message.reply_text(t("onboarding.dinner_invalid", locale=locale))
                    return True
                _set_meal_window(routine, "dinner", t)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_enabled"
        await update.message.reply_text(
            t("onboarding.ask_workout_enabled", locale=locale),
            reply_markup=yes_no_keyboard(locale),
        )
        return True

    if step == "workout_enabled":
        if is_skip(text):
            enabled = routine.workout_enabled
        else:
            enabled = parse_yes_no(text)
            if enabled is None:
                await update.message.reply_text(
                    t("common.reply_yes_no", locale=locale),
                    reply_markup=yes_no_keyboard(locale),
                )
                return True
        routine.workout_enabled = bool(enabled)
        db.add(routine)
        db.commit()
        if not routine.workout_enabled:
            context.user_data["onboarding_step"] = "goal"
            await update.message.reply_text(t("onboarding.ask_goal", locale=locale))
            return True
        context.user_data["onboarding_step"] = "workout_block"
        await update.message.reply_text(t("onboarding.ask_workout_block", locale=locale))
        return True

    if step == "workout_block":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text(t("onboarding.workout_block_invalid", locale=locale))
                return True
            routine.workout_block_min = max(30, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_travel"
        await update.message.reply_text(t("onboarding.ask_workout_travel", locale=locale))
        return True

    if step == "workout_travel":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text(t("onboarding.workout_travel_invalid", locale=locale))
                return True
            routine.workout_travel_oneway_min = max(0, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_sunday"
        await update.message.reply_text(
            t("onboarding.ask_workout_sunday", locale=locale),
            reply_markup=yes_no_keyboard(locale),
        )
        return True

    if step == "workout_sunday":
        if not is_skip(text):
            yes = parse_yes_no(text)
            if yes is None:
                await update.message.reply_text(
                    t("common.reply_yes_no", locale=locale),
                    reply_markup=yes_no_keyboard(locale),
                )
                return True
            routine.workout_no_sunday = not yes
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "goal"
        await update.message.reply_text(t("onboarding.ask_goal", locale=locale))
        return True

    if step == "goal":
        answer = text.strip()
        if is_skip(text):
            user.onboarded = True
            db.add(user)
            db.commit()
            context.user_data.pop("onboarding_step", None)
            await update.message.reply_text(t("onboarding.done", locale=locale))
            return True
        user.primary_focus = answer[:120]
        db.add(user)
        db.commit()
        suggestions = suggest_routine_steps(
            answer,
            settings.OPENAI_API_KEY,
            settings.OPENAI_CHAT_MODEL,
        ) or _suggest_routine_steps(answer)
        context.user_data["suggested_steps"] = suggestions
        context.user_data["onboarding_step"] = "suggest"
        suggestion_text = ", ".join(suggestions)
        await update.message.reply_text(
            t("onboarding.suggest_prompt", locale=locale, suggestions=suggestion_text),
            reply_markup=yes_no_keyboard(locale),
        )
        return True

    if step == "suggest":
        flags = parse_reply(text)
        if is_skip(text) or flags.is_no:
            user.onboarded = True
            db.add(user)
            db.commit()
            context.user_data.pop("onboarding_step", None)
            await update.message.reply_text(t("onboarding.done", locale=locale))
            return True

        default_steps = t_list("onboarding.default_steps", locale=locale) or ["Стакан воды", "Растяжка", "Завтрак", "План дня"]
        if flags.is_yes:
            steps = context.user_data.get("suggested_steps") or default_steps
        else:
            steps = split_items(text)
            if not steps:
                steps = context.user_data.get("suggested_steps") or default_steps

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
        await update.message.reply_text(t("onboarding.saved", locale=locale))
        return True

    return False

async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    day = now_local_naive().date()
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        routine = crud.get_routine(db, user.id)
        ensure_day_anchors(db, user.id, day, routine)

        tasks = crud.list_tasks_for_day(db, user.id, day)
        routine_tasks = [t for t in tasks if t.task_type == "system" and (t.idempotency_key or "").startswith("routine:")]

        if not routine_tasks:
            await update.message.reply_text(t("routine.morning.empty", locale=locale))
            return

        routine_tasks.sort(key=lambda t: t.planned_start or dt.datetime.max)
        lines = [t("routine.morning.header", locale=locale)]
        for t in routine_tasks:
            s = t.planned_start.strftime("%H:%M") if t.planned_start else "?"
            e = t.planned_end.strftime("%H:%M") if t.planned_end else "?"
            lines.append(
                t("routine.morning.item", locale=locale, start=s, end=e, title=t.title, task_id=t.id)
            )

        await update.message.reply_text("\n".join(lines))

async def cmd_routine_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text(t("routine.add.usage", locale="ru"))
        return

    try:
        offset_min = int(context.args[0])
        duration_min = int(context.args[1])
    except ValueError:
        await update.message.reply_text(t("routine.add.numbers_invalid", locale="ru"))
        return

    rest = " ".join(context.args[2:]).strip()
    title = rest
    kind = "morning"
    if "|" in rest:
        title, kind = [p.strip() for p in rest.split("|", 1)]

    if not title:
        await update.message.reply_text(t("routine.add.title_empty", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
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

    await update.message.reply_text(
        t("routine.add.added", locale=locale, title=step.title, step_id=step.id)
    )

async def cmd_routine_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        steps = crud.list_routine_steps(db, user.id, active_only=False)
        if not steps:
            await update.message.reply_text(t("routine.list.empty", locale=locale))
            return

        lines = [t("routine.list.header", locale=locale)]
        for s in steps:
            lines.append(
                t(
                    "routine.list.item",
                    locale=locale,
                    step_id=s.id,
                    offset=s.offset_min,
                    duration=s.duration_min,
                    kind=s.kind,
                    title=s.title,
                )
            )
        await update.message.reply_text("\n".join(lines))

async def cmd_routine_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("routine.del.usage", locale="ru"))
        return

    try:
        step_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("routine.del.id_invalid", locale="ru"))
        return

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
        ok = crud.delete_routine_step(db, user.id, step_id)
        if not ok:
            await update.message.reply_text(t("routine.del.not_found", locale=locale))
            return

    await update.message.reply_text(t("routine.del.deleted", locale=locale, step_id=step_id))
