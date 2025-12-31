import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.bot.parsing.text import parse_weekday
from app.bot.parsing.values import parse_float_value, parse_int_value


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /health checkin|today ...")
        return

    action = context.args[0].lower()
    args = context.args[1:]
    day = _now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return

        if action == "checkin":
            if len(args) < 2:
                await update.message.reply_text("Использование: /health checkin <сон_часы> <энергия_1-5> [вода_мл]")
                return
            sleep = parse_float_value(args[0])
            energy = parse_int_value(args[1])
            water = parse_int_value(args[2]) if len(args) >= 3 else None
            if sleep is None or energy is None:
                await update.message.reply_text("Пример: /health checkin 7.5 4 1500")
                return
            sleep_hours = sleep
            energy_level = max(1, min(5, energy))
            crud.upsert_daily_checkin(
                db,
                user.id,
                day,
                sleep_hours=sleep_hours,
                energy_level=energy_level,
                water_ml=water,
            )
            await update.message.reply_text("Чек‑ин сохранен.")
            return

        if action == "today":
            checkin = crud.get_daily_checkin(db, user.id, day)
            habits = crud.list_habits(db, user.id, active_only=True)
            lines = ["Здоровье сегодня:"]
            if checkin:
                sleep_val = f"{checkin.sleep_hours:.1f}" if checkin.sleep_hours is not None else "—"
                lines.append(f"- сон: {sleep_val} ч")
                lines.append(f"- энергия: {checkin.energy_level or '—'}/5")
                lines.append(f"- вода: {checkin.water_ml or '—'} мл")
            else:
                lines.append("- чек‑ина нет")
            if habits:
                lines.append("Привычки:")
                for h in habits:
                    total = crud.sum_habit_for_day(db, h.id, day)
                    target = f"/{h.target_per_day}" if h.target_per_day else ""
                    unit = f" {h.unit}" if h.unit else ""
                    lines.append(f"- {h.name}: {total}{unit}{target}")
            await update.message.reply_text("\n".join(lines))
            return

    await update.message.reply_text("Использование: /health checkin|today ...")

async def cmd_habit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /habit add|log|list ...")
        return

    action = context.args[0].lower()
    args = context.args[1:]
    day = _now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return

        if action == "list":
            habits = crud.list_habits(db, user.id, active_only=True)
            if not habits:
                await update.message.reply_text("Привычек пока нет. Используйте /habit add.")
                return
            lines = ["Привычки:"]
            for h in habits:
                target = f"/{h.target_per_day}" if h.target_per_day else ""
                unit = f" {h.unit}" if h.unit else ""
                lines.append(f"- {h.name} {target}{unit}".strip())
            await update.message.reply_text("\n".join(lines))
            return

        if action == "add":
            if not args:
                await update.message.reply_text("Использование: /habit add <название> [цель] [единица]")
                return
            target = None
            unit = None
            if args and parse_int_value(args[-1]) is not None:
                target = parse_int_value(args[-1])
                args = args[:-1]
                if args and args[-1].isalpha():
                    unit = args[-1]
                    args = args[:-1]
            name = " ".join(args).strip()
            if not name:
                await update.message.reply_text("Название не может быть пустым.")
                return
            habit = crud.upsert_habit(db, user.id, name=name, target_per_day=target, unit=unit)
            await update.message.reply_text(f"Привычка сохранена: {habit.name}")
            return

        if action == "log":
            if not args:
                await update.message.reply_text("Использование: /habit log <название> [значение]")
                return
            value = 1
            if parse_int_value(args[-1]) is not None:
                value = parse_int_value(args[-1]) or 1
                args = args[:-1]
            name = " ".join(args).strip()
            if not name:
                await update.message.reply_text("Название не может быть пустым.")
                return
            habit = crud.get_habit_by_name(db, user.id, name)
            if not habit:
                habit = crud.upsert_habit(db, user.id, name=name)
            crud.log_habit(db, user.id, habit.id, day, value=value)
            await update.message.reply_text(f"Записано: {habit.name} (+{value})")
            return

    await update.message.reply_text("Использование: /habit add|log|list ...")

async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /workout today|show|set|clear|list ...")
        return

    action = context.args[0].lower()
    args = context.args[1:]

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
            weekday = parse_weekday(args[0])
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
            weekday = parse_weekday(args[0])
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
            weekday = parse_weekday(args[0])
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
