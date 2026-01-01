import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.bot.parsing.text import parse_weekday
from app.bot.parsing.values import parse_float_value, parse_int_value
from app.bot.utils import now_local_naive as _now_local_naive
from app.i18n.core import locale_for_user, t


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("health.usage", locale="ru"))
        return

    action = context.args[0].lower()
    args = context.args[1:]
    day = _now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)

        if action == "checkin":
            if len(args) < 2:
                await update.message.reply_text(t("health.checkin.usage", locale=locale))
                return
            sleep = parse_float_value(args[0])
            energy = parse_int_value(args[1])
            water = parse_int_value(args[2]) if len(args) >= 3 else None
            if sleep is None or energy is None:
                await update.message.reply_text(t("health.checkin.example", locale=locale))
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
            await update.message.reply_text(t("health.checkin.saved", locale=locale))
            return

        if action == "today":
            checkin = crud.get_daily_checkin(db, user.id, day)
            habits = crud.list_habits(db, user.id, active_only=True)
            lines = [t("health.today.header", locale=locale)]
            if checkin:
                sleep_val = f"{checkin.sleep_hours:.1f}" if checkin.sleep_hours is not None else t("common.na", locale=locale)
                energy_val = checkin.energy_level or t("common.na", locale=locale)
                water_val = checkin.water_ml or t("common.na", locale=locale)
                lines.append(t("health.today.sleep", locale=locale, value=sleep_val))
                lines.append(t("health.today.energy", locale=locale, value=energy_val))
                lines.append(t("health.today.water", locale=locale, value=water_val))
            else:
                lines.append(t("health.today.no_checkin", locale=locale))
            if habits:
                lines.append(t("health.today.habits_header", locale=locale))
                for h in habits:
                    total = crud.sum_habit_for_day(db, h.id, day)
                    target = f"/{h.target_per_day}" if h.target_per_day else ""
                    unit = f" {h.unit}" if h.unit else ""
                    lines.append(
                        t(
                            "health.today.habit_item",
                            locale=locale,
                            name=h.name,
                            total=total,
                            unit=unit,
                            target=target,
                        )
                    )
            await update.message.reply_text("\n".join(lines))
            return

    await update.message.reply_text(t("health.usage", locale=locale))

async def cmd_habit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("habit.usage", locale="ru"))
        return

    action = context.args[0].lower()
    args = context.args[1:]
    day = _now_local_naive().date()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)

        if action == "list":
            habits = crud.list_habits(db, user.id, active_only=True)
            if not habits:
                await update.message.reply_text(t("habit.list.empty", locale=locale))
                return
            lines = [t("habit.list.header", locale=locale)]
            for h in habits:
                target = f"/{h.target_per_day}" if h.target_per_day else ""
                unit = f" {h.unit}" if h.unit else ""
                lines.append(
                    t(
                        "habit.list.item",
                        locale=locale,
                        name=h.name,
                        target=target,
                        unit=unit,
                    ).strip()
                )
            await update.message.reply_text("\n".join(lines))
            return

        if action == "add":
            if not args:
                await update.message.reply_text(t("habit.add.usage", locale=locale))
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
                await update.message.reply_text(t("habit.add.title_empty", locale=locale))
                return
            habit = crud.upsert_habit(db, user.id, name=name, target_per_day=target, unit=unit)
            await update.message.reply_text(t("habit.add.saved", locale=locale, name=habit.name))
            return

        if action == "log":
            if not args:
                await update.message.reply_text(t("habit.log.usage", locale=locale))
                return
            value = 1
            if parse_int_value(args[-1]) is not None:
                value = parse_int_value(args[-1]) or 1
                args = args[:-1]
            name = " ".join(args).strip()
            if not name:
                await update.message.reply_text(t("habit.log.title_empty", locale=locale))
                return
            habit = crud.get_habit_by_name(db, user.id, name)
            if not habit:
                habit = crud.upsert_habit(db, user.id, name=name)
            crud.log_habit(db, user.id, habit.id, day, value=value)
            await update.message.reply_text(
                t("habit.log.saved", locale=locale, name=habit.name, value=value)
            )
            return

    await update.message.reply_text(t("habit.usage", locale=locale))

async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("workout.usage", locale="ru"))
        return

    action = context.args[0].lower()
    args = context.args[1:]

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)

        if action == "today":
            weekday = _now_local_naive().weekday()
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text(t("workout.today.none", locale=locale))
                return
            text = plan.details or t("workout.details.empty", locale=locale)
            await update.message.reply_text(
                t("workout.today.show", locale=locale, title=plan.title, details=text)
            )
            return

        if action == "show":
            if not args:
                await update.message.reply_text(t("workout.show.usage", locale=locale))
                return
            weekday = parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text(t("workout.weekday_invalid", locale=locale))
                return
            plan = crud.get_workout_plan(db, user.id, weekday)
            if not plan or not plan.is_active:
                await update.message.reply_text(t("workout.show.none", locale=locale))
                return
            text = plan.details or t("workout.details.empty", locale=locale)
            await update.message.reply_text(
                t("workout.show.result", locale=locale, title=plan.title, details=text)
            )
            return

        if action == "set":
            if len(args) < 2:
                await update.message.reply_text(t("workout.set.usage", locale=locale))
                return
            weekday = parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text(t("workout.weekday_invalid", locale=locale))
                return
            rest = " ".join(args[1:])
            title = rest
            details = None
            if "|" in rest:
                title, details = [p.strip() for p in rest.split("|", 1)]
            plan = crud.set_workout_plan(db, user.id, weekday, title=title, details=details)
            await update.message.reply_text(
                t("workout.set.saved", locale=locale, weekday=plan.weekday, title=plan.title)
            )
            return

        if action == "clear":
            if not args:
                await update.message.reply_text(t("workout.clear.usage", locale=locale))
                return
            weekday = parse_weekday(args[0])
            if weekday is None:
                await update.message.reply_text(t("workout.weekday_invalid", locale=locale))
                return
            ok = crud.clear_workout_plan(db, user.id, weekday)
            if not ok:
                await update.message.reply_text(t("workout.clear.not_found", locale=locale))
                return
            await update.message.reply_text(t("workout.clear.done", locale=locale))
            return

        if action == "list":
            plans = crud.list_workout_plans(db, user.id)
            if not plans:
                await update.message.reply_text(t("workout.list.empty", locale=locale))
                return
            lines = [t("workout.list.header", locale=locale)]
            for plan in plans:
                lines.append(
                    t("workout.list.item", locale=locale, weekday=plan.weekday, title=plan.title)
                )
            await update.message.reply_text("\n".join(lines))
            return

    await update.message.reply_text(t("workout.usage", locale=locale))
