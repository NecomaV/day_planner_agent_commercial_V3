from telegram import ReplyKeyboardMarkup

from app.i18n.core import t


def yes_no_keyboard(locale: str = "ru") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[t("common.yes", locale=locale), t("common.no", locale=locale)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def yes_no_cancel_keyboard(locale: str = "ru") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [t("common.yes", locale=locale), t("common.no", locale=locale)],
            [t("common.cancel", locale=locale)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
