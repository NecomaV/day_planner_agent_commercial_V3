from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-?е|го)?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\b", re.IGNORECASE)
CHECKLIST_RE = re.compile(r"\b(checklist|steps|чек[- ]?лист|чеклист|список|пункты):\s*(.+)$", re.IGNORECASE)
NEXT_WEEKDAY_RE = re.compile(
    r"\bnext\s+(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
RUS_WEEKDAY_RE = re.compile(
    r"\b(следующ(?:ий|ая|ее)\s+)?(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b",
    re.IGNORECASE,
)

WEEKDAY_MAP = {
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
}
RUS_WEEKDAY_MAP = {
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

MONTH_MAP = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


@dataclass(frozen=True)
class QuickCaptureResult:
    title: str
    due_at: dt.datetime | None
    checklist_items: list[str]


def _normalize_year(day: int, month: int, now: dt.datetime) -> int:
    year = now.year
    try:
        candidate = dt.date(year, month, day)
    except ValueError:
        return year
    if candidate < now.date():
        return year + 1
    return year


def _parse_date(text: str, now: dt.datetime) -> dt.date | None:
    m = DATE_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None

    m = DAY_MONTH_RE.search(text)
    if m:
        day = int(m.group(1))
        month = MONTH_MAP.get(m.group(2).lower())
        if not month:
            return None
        year = _normalize_year(day, month, now)
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    lower = text.lower()
    if "tomorrow" in lower or "завтра" in lower:
        return now.date() + dt.timedelta(days=1)
    if "today" in lower or "сегодня" in lower:
        return now.date()
    if "послезавтра" in lower:
        return now.date() + dt.timedelta(days=2)

    m = NEXT_WEEKDAY_RE.search(text)
    if m:
        target = WEEKDAY_MAP.get(m.group(1).lower())
        if target is None:
            return None
        days_ahead = (target - now.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return now.date() + dt.timedelta(days=days_ahead)

    m = RUS_WEEKDAY_RE.search(text)
    if m:
        token = m.group(2).lower()
        target = RUS_WEEKDAY_MAP.get(token)
        if target is None:
            return None
        days_ahead = (target - now.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return now.date() + dt.timedelta(days=days_ahead)

    return None


def _parse_time(text: str) -> dt.time | None:
    m = TIME_RE.search(text)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    lower = text.lower()
    if re.search(r"\b(pm|вечера|дня)\b", lower) and hh < 12:
        hh += 12
    if re.search(r"\b(am|утра|ночи)\b", lower) and hh == 12:
        hh = 0
    if hh > 23 or mm > 59:
        return None
    return dt.time(hh, mm)


def _extract_checklist(text: str) -> tuple[str, list[str]]:
    m = CHECKLIST_RE.search(text)
    if not m:
        return text, []
    tail = m.group(2)
    items = [i.strip() for i in re.split(r"[;,]", tail) if i.strip()]
    cleaned = text[: m.start()].strip()
    return cleaned, items


def parse_quick_task(text: str, now: dt.datetime) -> QuickCaptureResult:
    original = text.strip()
    cleaned, checklist = _extract_checklist(original)

    date = _parse_date(cleaned, now)
    time_text = DATE_RE.sub("", cleaned)
    time_text = DAY_MONTH_RE.sub("", time_text)
    time = _parse_time(time_text)

    due_at = None
    if time and not date:
        candidate = dt.datetime.combine(now.date(), time)
        date = candidate.date() if candidate >= now else (now.date() + dt.timedelta(days=1))
    if date and not time:
        time = dt.time(9, 0)
    if date and time:
        due_at = dt.datetime.combine(date, time)

    stripped = cleaned
    stripped = DATE_RE.sub("", stripped)
    stripped = DAY_MONTH_RE.sub("", stripped)
    stripped = NEXT_WEEKDAY_RE.sub("", stripped)
    stripped = RUS_WEEKDAY_RE.sub("", stripped)
    stripped = re.sub(r"\b(today|tomorrow|сегодня|завтра|послезавтра)\b", "", stripped, flags=re.IGNORECASE)
    stripped = TIME_RE.sub("", stripped)
    stripped = re.sub(r"\b(at|by|в|на|до)\b", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(
        r"\b(please|remind me to|i need to|i need|need to|need|add|добавь|добавить|напомни|напомнить|нужно|надо|сделать|задача|задачи)\b",
        "",
        stripped,
        flags=re.IGNORECASE,
    )
    title = " ".join(stripped.split()).strip()
    if not title:
        title = original

    return QuickCaptureResult(title=title, due_at=due_at, checklist_items=checklist)
