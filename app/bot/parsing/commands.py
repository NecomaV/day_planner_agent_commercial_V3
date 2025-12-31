import re
from typing import Optional


def parse_command_text(text: str) -> tuple[str, list[str]] | None:
    raw = text.strip()
    if not raw.startswith("/"):
        return None
    parts = raw.lstrip("/").split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def parse_yes_no(text: str) -> Optional[bool]:
    cleaned = re.sub(r"[^\w\s]", " ", text.strip().lower())
    tokens = {t for t in cleaned.split() if t}
    yes_words = {
        "\u0434\u0430",
        "\u0430\u0433\u0430",
        "\u0430\u0433\u0430\u0448\u0430",
        "\u043e\u043a",
        "\u0445\u043e\u0440\u043e\u0448\u043e",
        "\u043a\u043e\u043d\u0435\u0447\u043d\u043e",
        "\u0432\u0435\u0440\u043d\u043e",
        "\u0434\u0430\u0432\u0430\u0439",
        "yes",
        "y",
        "ok",
        "sure",
    }
    no_words = {
        "\u043d\u0435\u0442",
        "\u043d\u0435",
        "\u043d\u0435\u0442\u043a\u0430",
        "\u043d\u0435 \u043d\u0430\u0434\u043e",
        "\u043e\u0442\u043c\u0435\u043d\u0430",
        "\u0441\u0442\u043e\u043f",
        "no",
        "n",
    }
    has_yes = bool(tokens & yes_words)
    has_no = bool(tokens & no_words)
    if has_yes and not has_no:
        return True
    if has_no and not has_yes:
        return False
    return None
