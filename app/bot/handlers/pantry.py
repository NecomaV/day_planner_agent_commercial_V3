from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.i18n.core import locale_for_user, t
from app.services.meal_suggest import suggest_meals


async def cmd_pantry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(t("pantry.usage", locale="ru"))
        return

    action = context.args[0].lower()
    rest = " ".join(context.args[1:]).strip()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)

        if action in {"list", "ls"}:
            items = crud.list_pantry_items(db, user.id)
            if not items:
                await update.message.reply_text(t("pantry.empty", locale=locale))
                return
            lines = [t("pantry.list.header", locale=locale)]
            for item in items:
                qty = f" ({item.quantity})" if item.quantity else ""
                lines.append(t("pantry.list.item", locale=locale, name=item.name, qty=qty))
            await update.message.reply_text("\n".join(lines))
            return

        if action == "add":
            if not rest:
                await update.message.reply_text(t("pantry.add.usage", locale=locale))
                return
            name = rest
            quantity = None
            if "=" in rest:
                name, quantity = [p.strip() for p in rest.split("=", 1)]
            elif ":" in rest:
                name, quantity = [p.strip() for p in rest.split(":", 1)]
            crud.upsert_pantry_item(db, user.id, name=name, quantity=quantity)
            await update.message.reply_text(t("pantry.added", locale=locale, name=name))
            return

        if action in {"remove", "del", "delete"}:
            if not rest:
                await update.message.reply_text(t("pantry.remove.usage", locale=locale))
                return
            ok = crud.remove_pantry_item(db, user.id, name=rest)
            if not ok:
                await update.message.reply_text(t("pantry.remove.not_found", locale=locale))
                return
            await update.message.reply_text(t("pantry.remove.done", locale=locale, name=rest))
            return

    await update.message.reply_text(t("pantry.usage", locale="ru"))

async def cmd_breakfast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
        if not user:
            return
        locale = locale_for_user(user)
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
            lines.append(t("pantry.breakfast.item_missing", locale=locale, name=s["name"], missing=missing))
        else:
            lines.append(t("pantry.breakfast.item_ready", locale=locale, name=s["name"]))
    await update.message.reply_text("\n".join(lines))
