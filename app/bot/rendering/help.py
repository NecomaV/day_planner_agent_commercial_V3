from app.i18n.core import t


def start_help_message(locale: str = "ru") -> str:
    return t("help.start", locale=locale)
