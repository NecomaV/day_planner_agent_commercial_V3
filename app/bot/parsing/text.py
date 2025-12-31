import re


def _extract_task_ids(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\b\d+\b", text)]


def _is_skip(text: str) -> bool:
    return text.strip().lower() in {"skip", "later", "пропустить", "потом", "не знаю", "не уверен"}


def _is_no_due(text: str) -> bool:
    lower = text.strip().lower()
    return lower in {"без срока", "нет срока", "без дедлайна", "no due", "no deadline"}


def _parse_weekday(value: str) -> int | None:
    value = value.strip().lower()
    mapping = {
        "0": 0,
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
        "пн": 0,
        "понедельник": 0,
        "вт": 1,
        "вторник": 1,
        "ср": 2,
        "среда": 2,
        "чт": 3,
        "четверг": 3,
        "пт": 4,
        "пятница": 4,
        "сб": 5,
        "суббота": 5,
        "вс": 6,
        "воскресенье": 6,
    }
    return mapping.get(value)


def _split_items(text: str) -> list[str]:
    items = [i.strip() for i in re.split(r"[;,]", text) if i.strip()]
    if len(items) == 1 and re.search(r"\band\b", items[0], re.IGNORECASE):
        items = [i.strip() for i in re.split(r"\band\b", items[0], flags=re.IGNORECASE) if i.strip()]
    return items


def _extract_routine_items(text: str) -> list[str]:
    lower = text.lower()
    triggers = [
        "every morning",
        "each morning",
        "add to routine",
        "morning routine",
        "routine:",
        "каждое утро",
        "утренняя рутина",
        "добавь в рутину",
        "в рутину",
        "рутина:",
    ]
    if not any(t in lower for t in triggers):
        return []
    cleaned = text
    for t in triggers:
        cleaned = re.sub(re.escape(t), "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(":", " ")
    return _split_items(cleaned)


extract_task_ids = _extract_task_ids
is_skip = _is_skip
is_no_due = _is_no_due
parse_weekday = _parse_weekday
split_items = _split_items
extract_routine_items = _extract_routine_items
