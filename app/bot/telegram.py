from telegram import Bot

from app.settings import settings


def get_bot() -> Bot:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    return Bot(token=token)
