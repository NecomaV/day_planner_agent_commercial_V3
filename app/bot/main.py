from __future__ import annotations

import logging
from pathlib import Path

from telegram.ext import Application

from app.bot.handlers import register_handlers
from app.settings import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def build_application() -> Application:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        hint = (
            "TELEGRAM_BOT_TOKEN is missing.\n"
            f"Looked for .env at: {ENV_PATH}\n"
            f"Current working directory: {Path.cwd()}\n"
            "Fix:\n"
            "1) Ensure file name is exactly '.env' (not .env.txt)\n"
            "2) Ensure it contains: TELEGRAM_BOT_TOKEN=...\n"
            "3) Restart the bot\n"
        )
        raise RuntimeError(hint)

    app = Application.builder().token(token).build()
    register_handlers(app)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("day_planner_bot")
    app = build_application()
    logger.info("Bot started")
    app.run_polling(close_loop=False)


__all__ = ["build_application", "main"]
