from __future__ import annotations

import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user, get_user
from app.bot.parsing.commands import parse_yes_no
from app.bot.parsing.text import is_skip, split_items
from app.bot.parsing.values import parse_int_value
from app.bot.utils import now_local_naive
from app.services.autoplan import ensure_day_anchors
from app.services.ai_intent import suggest_routine_steps


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["onboarding_step"] = "name"
    await update.message.reply_text(
        "Добро пожаловать! Давайте настроим профиль. Как вас зовут? (можно написать «пропустить»)"
    )

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

    if step == "name":
        if is_skip(text):
            user.full_name = None
        else:
            user.full_name = text.strip()[:120]
        db.add(user)
        db.commit()
        context.user_data["onboarding_step"] = "timezone"
        await update.message.reply_text(
            "Ваш часовой пояс? Например: Europe/Moscow или Asia/Almaty (можно «пропустить»)."
        )
        return True

    if step == "timezone":
        if not is_skip(text):
            user.timezone = text.strip()
        db.add(user)
        db.commit()
        context.user_data["onboarding_step"] = "wake"
        await update.message.reply_text("Во сколько обычно просыпаетесь? (HH:MM)")
        return True

    if step == "wake":
        if is_skip(text):
            context.user_data["onboarding_step"] = "bed"
            await update.message.reply_text("Во сколько обычно ложитесь спать? (HH:MM)")
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
        if is_skip(text):
            context.user_data["onboarding_step"] = "workday"
            await update.message.reply_text("Во сколько обычно рабочий день? Например 09:00-18:00 (можно «пропустить»).")
            return True
        t = _parse_time_value(text) or None
        if not t:
            await update.message.reply_text("Введите время в формате HH:MM, например 23:30")
            return True
        bed_str = f"{t.hour:02d}:{t.minute:02d}"
        routine.sleep_target_bedtime = bed_str
        db.add(routine)
        db.commit()
        context.user_data["onboarding_step"] = "workday"
        await update.message.reply_text("Во сколько обычно рабочий день? Например 09:00-18:00 (можно «пропустить»).")
        return True

    if step == "workday":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if not time_range:
                await update.message.reply_text("Нужен диапазон времени, например 09:00-18:00")
                return True
            start, end = time_range
            routine.workday_start = start.strftime("%H:%M")
            routine.workday_end = end.strftime("%H:%M")
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "latest_end"
        await update.message.reply_text("До какого времени можно ставить задачи? (HH:MM или «пропустить»)")
        return True

    if step == "latest_end":
        if not is_skip(text):
            t = _parse_time_value(text) or None
            if not t:
                await update.message.reply_text("Введите время в формате HH:MM, например 19:00")
                return True
            routine.latest_task_end = f"{t.hour:02d}:{t.minute:02d}"
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "task_buffer"
        await update.message.reply_text("Нужен буфер между задачами? Сколько минут? (например 15)")
        return True

    if step == "task_buffer":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text("Введите число минут, например 15")
                return True
            routine.task_buffer_after_min = max(0, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "lunch"
        await update.message.reply_text("Во сколько обычно обедаете? (HH:MM или диапазон)")
        return True

    if step == "lunch":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if time_range:
                _set_meal_window_range(routine, "lunch", time_range[0], time_range[1])
            else:
                t = _parse_time_value(text) or None
                if not t:
                    await update.message.reply_text("Введите время в формате HH:MM, например 13:00")
                    return True
                _set_meal_window(routine, "lunch", t)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "dinner"
        await update.message.reply_text("Во сколько обычно ужинаете? (HH:MM или диапазон)")
        return True

    if step == "dinner":
        if not is_skip(text):
            time_range = _parse_time_range(text)
            if time_range:
                _set_meal_window_range(routine, "dinner", time_range[0], time_range[1])
            else:
                t = _parse_time_value(text) or None
                if not t:
                    await update.message.reply_text("Введите время в формате HH:MM, например 19:30")
                    return True
                _set_meal_window(routine, "dinner", t)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_enabled"
        await update.message.reply_text("Есть тренировки? (да/нет)")
        return True

    if step == "workout_enabled":
        if is_skip(text):
            enabled = routine.workout_enabled
        else:
            enabled = parse_yes_no(text)
            if enabled is None:
                await update.message.reply_text("Ответьте «да» или «нет».")
                return True
        routine.workout_enabled = bool(enabled)
        db.add(routine)
        db.commit()
        if not routine.workout_enabled:
            context.user_data["onboarding_step"] = "goal"
            await update.message.reply_text("Какой сейчас главный фокус? (работа, фитнес, семья, учеба, другое)")
            return True
        context.user_data["onboarding_step"] = "workout_block"
        await update.message.reply_text("Сколько минут длится тренировка с душем? (например 90)")
        return True

    if step == "workout_block":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text("Введите число минут, например 90")
                return True
            routine.workout_block_min = max(30, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_travel"
        await update.message.reply_text("Сколько минут на дорогу в одну сторону? (например 15)")
        return True

    if step == "workout_travel":
        if not is_skip(text):
            minutes = parse_int_value(text)
            if minutes is None:
                await update.message.reply_text("Введите число минут, например 15")
                return True
            routine.workout_travel_oneway_min = max(0, minutes)
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "workout_sunday"
        await update.message.reply_text("Тренируетесь по воскресеньям? (да/нет)")
        return True

    if step == "workout_sunday":
        if not is_skip(text):
            yes = parse_yes_no(text)
            if yes is None:
                await update.message.reply_text("Ответьте «да» или «нет».")
                return True
            routine.workout_no_sunday = not yes
            db.add(routine)
            db.commit()
        context.user_data["onboarding_step"] = "goal"
        await update.message.reply_text("Какой сейчас главный фокус? (работа, фитнес, семья, учеба, другое)")
        return True

    if step == "goal":
        answer = text.strip()
        if is_skip(text):
            user.onboarded = True
            db.add(user)
            db.commit()
            context.user_data.pop("onboarding_step", None)
            await update.message.reply_text("Готово. Теперь можно отправлять задачи или использовать /morning.")
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
            "Предлагаемая рутина: " + suggestion_text + "\n"
            "Ответьте <да>, чтобы принять, или пришлите свой список."
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
            steps = split_items(text)
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

async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    day = now_local_naive().date()
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        ok = crud.delete_routine_step(db, user.id, step_id)
        if not ok:
            await update.message.reply_text("Шаг рутины не найден")
            return

    await update.message.reply_text(f"Шаг рутины удален (id={step_id})")
