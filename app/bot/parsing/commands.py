from typing import Optional

from app.bot.parsing.ru_reply import parse_reply


def parse_command_text(text: str) -> tuple[str, list[str]] | None:
    raw = text.strip()
    if not raw.startswith("/"):
        return None
    parts = raw.lstrip("/").split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def parse_yes_no(text: str) -> Optional[bool]:
    flags = parse_reply(text)
    if flags.is_yes and not flags.is_no:
        return True
    if flags.is_no and not flags.is_yes:
        return False
    return None
