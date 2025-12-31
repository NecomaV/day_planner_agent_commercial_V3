from telegram import Update
from telegram.ext import ContextTypes

from app import crud
from app.bot.context import get_db_session, get_ready_user
from app.bot.handlers.routine import start_onboarding
from app.services.meal_suggest import suggest_meals


async def cmd_pantry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /pantry add|remove|list <продукт>")
        return

    action = context.args[0].lower()
    rest = " ".join(context.args[1:]).strip()

    with get_db_session() as db:
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
        user = await get_ready_user(update, context, db, start_onboarding=start_onboarding)
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
