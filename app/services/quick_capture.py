from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass


DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
CHECKLIST_RE = re.compile(r"\b(checklist|steps):\s*(.+)$", re.IGNORECASE)
NEXT_WEEKDAY_RE = re.compile(r"\bnext\s+(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)


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


@dataclass(frozen=True)
class QuickCaptureResult:
    title: str
    due_at: dt.datetime | None
    checklist_items: list[str]


def _parse_date(text: str, now: dt.datetime) -> dt.date | None:
    m = DATE_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None

    lower = text.lower()
    if "tomorrow" in lower:
        return now.date() + dt.timedelta(days=1)
    if "today" in lower:
        return now.date()

    m = NEXT_WEEKDAY_RE.search(text)
    if m:
        target = WEEKDAY_MAP.get(m.group(1).lower())
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
    suffix = (m.group(3) or "").lower()
    if suffix == "pm" and hh < 12:
        hh += 12
    if suffix == "am" and hh == 12:
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
    stripped = NEXT_WEEKDAY_RE.sub("", stripped)
    stripped = re.sub(r"\b(today|tomorrow)\b", "", stripped, flags=re.IGNORECASE)
    stripped = TIME_RE.sub("", stripped)
    stripped = re.sub(r"\b(at|by)\b", "", stripped, flags=re.IGNORECASE)
    title = " ".join(stripped.split()).strip()
    if not title:
        title = original

    return QuickCaptureResult(title=title, due_at=due_at, checklist_items=checklist)
