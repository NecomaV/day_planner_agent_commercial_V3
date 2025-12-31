import datetime as dt
import re


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


MONTH_RE = re.compile(r"\b(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\b", re.IGNORECASE)


DATE_LIST_RE = re.compile(
    r"((?:\d{1,2}(?:-?е|го)?\s*(?:,|и|или)?\s*)+)\s*(?:числа|число)?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
    re.IGNORECASE,
)


DATE_RANGE_RE = re.compile(
    r"\b(?:с|со)\s+(\d{1,2})(?:-?е|го)?\s+(?:по|до)\s+(\d{1,2})(?:-?е|го)?\s*(?:числа|число)?\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)?",
    re.IGNORECASE,
)


DATE_TOKEN_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


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


def _normalize_year(day: int, month: int, now: dt.datetime) -> int:
    year = now.year
    try:
        candidate = dt.date(year, month, day)
    except ValueError:
        return year
    if candidate < now.date():
        return year + 1
    return year


def _extract_dates_from_text(text: str, now: dt.datetime) -> list[dt.date]:
    dates: set[dt.date] = set()
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        try:
            dates.add(dt.date.fromisoformat(m.group(1)))
        except ValueError:
            continue

    for match in DATE_RANGE_RE.finditer(text):
        start_day = int(match.group(1))
        end_day = int(match.group(2))
        month_token = match.group(3)
        month = MONTH_MAP.get(month_token.lower()) if month_token else now.month
        if not month:
            continue
        start = min(start_day, end_day)
        end = max(start_day, end_day)
        for day in range(start, end + 1):
            year = _normalize_year(day, month, now)
            try:
                dates.add(dt.date(year, month, day))
            except ValueError:
                continue

    for match in DATE_LIST_RE.finditer(text):
        days_raw = match.group(1)
        month_token = match.group(2).lower()
        month = MONTH_MAP.get(month_token)
        if not month:
            continue
        for day_str in re.findall(r"\d{1,2}", days_raw):
            day = int(day_str)
            year = _normalize_year(day, month, now)
            try:
                dates.add(dt.date(year, month, day))
            except ValueError:
                continue

    if not dates and re.search(r"\bчисл", text.lower()):
        for day_str in re.findall(r"\b\d{1,2}\b", text):
            day = int(day_str)
            month = now.month
            year = _normalize_year(day, month, now)
            try:
                dates.add(dt.date(year, month, day))
            except ValueError:
                continue

    return sorted(dates)


def _detect_relative_day(text: str, now: dt.datetime) -> dt.date | None:
    lower = text.lower()
    if "сегодня" in lower or "today" in lower:
        return now.date()
    if "послезавтра" in lower:
        return now.date() + dt.timedelta(days=2)
    if "завтра" in lower or "tomorrow" in lower:
        return now.date() + dt.timedelta(days=1)
    return None


def _parse_duration_minutes(text: str) -> int | None:
    lower = text.lower()
    m = re.search(r"\b(\d{1,3})\s*(мин|минут|минуты|m)\b", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})(?:[.,](\d))?\s*(час|часа|часов|h)\b", lower)
    if m:
        hours = int(m.group(1))
        frac = int(m.group(2) or 0)
        minutes = hours * 60 + (30 if frac >= 5 else 0)
        return minutes
    return None


def _parse_time_range(text: str) -> tuple[dt.time, dt.time] | None:
    lower = text.lower()
    range_match = re.search(
        r"(?:с\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:-|-|–|—|до|по)\s*(\d{1,2})(?::(\d{2}))?",
        lower,
    )
    if not range_match:
        return None
    meridian_pm = bool(re.search(r"\b(pm|вечера|дня)\b", lower))
    meridian_am = bool(re.search(r"\b(am|утра|ночи)\b", lower))

    def apply_meridian(hh: int) -> int:
        if meridian_pm and hh < 12:
            return hh + 12
        if meridian_am and hh == 12:
            return 0
        return hh

    h1 = apply_meridian(int(range_match.group(1)))
    m1 = int(range_match.group(2) or 0)
    h2 = apply_meridian(int(range_match.group(3)))
    m2 = int(range_match.group(4) or 0)
    if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
        return None
    return dt.time(h1, m1), dt.time(h2, m2)


def _parse_time_value(text: str) -> dt.time | None:
    lower = text.lower()
    if "полдень" in lower:
        return dt.time(12, 0)
    if "полночь" in lower:
        return dt.time(0, 0)
    range_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*[---]\s*(\d{1,2})(?::(\d{2}))?", lower)
    meridian_pm = bool(re.search(r"\b(pm|вечера|дня)\b", lower))
    meridian_am = bool(re.search(r"\b(am|утра|ночи)\b", lower))

    def apply_meridian(hh: int) -> int:
        if meridian_pm and hh < 12:
            return hh + 12
        if meridian_am and hh == 12:
            return 0
        return hh

    if range_match:
        h1 = int(range_match.group(1))
        m1 = int(range_match.group(2) or 0)
        h2 = int(range_match.group(3))
        m2 = int(range_match.group(4) or 0)
        h1 = apply_meridian(h1)
        h2 = apply_meridian(h2)
        if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
            return None
        start = dt.datetime.combine(dt.date.today(), dt.time(h1, m1))
        end = dt.datetime.combine(dt.date.today(), dt.time(h2, m2))
        if end <= start:
            return dt.time(h1, m1)
        midpoint = start + (end - start) / 2
        return midpoint.time().replace(second=0, microsecond=0)

    m = re.search(r"(\d{1,2})(?::(\d{2}))?", lower)
    if m:
        hh = apply_meridian(int(m.group(1)))
        mm = int(m.group(2) or 0)
        if hh > 23 or mm > 59:
            return None
        return dt.time(hh, mm)

    word_map = {
        "один": 1,
        "два": 2,
        "три": 3,
        "четыре": 4,
        "пять": 5,
        "шесть": 6,
        "семь": 7,
        "восемь": 8,
        "девять": 9,
        "десять": 10,
        "одиннадцать": 11,
        "двенадцать": 12,
        "тринадцать": 13,
        "четырнадцать": 14,
        "пятнадцать": 15,
        "шестнадцать": 16,
        "семнадцать": 17,
        "восемнадцать": 18,
        "девятнадцать": 19,
        "двадцать": 20,
        "полдня": 12,
    }
    for word, hour in word_map.items():
        if re.search(rf"\b{word}\b", lower):
            hh = apply_meridian(hour)
            if hh > 23:
                return None
            return dt.time(hh, 0)
    return None


def _has_due_intent(text: str) -> bool:
    return bool(re.search(r"\b(до|дедлайн|deadline|срок)\b", text.lower()))


def _resolve_date_for_time(now: dt.datetime, date: dt.date | None, time_value: dt.time) -> dt.datetime:
    base_date = date or now.date()
    candidate = dt.datetime.combine(base_date, time_value)
    if date is None and candidate < now:
        candidate = candidate + dt.timedelta(days=1)
    return candidate


def _format_date_list(dates: list[dt.date]) -> str:
    return ", ".join(sorted({d.isoformat() for d in dates}))


def _extract_task_timing(text: str, now: dt.datetime) -> tuple[dt.date | None, tuple[dt.time, dt.time] | None, dt.time | None, int | None]:
    dates = _extract_dates_from_text(text, now)
    date = dates[0] if dates else _detect_relative_day(text, now)
    time_range = _parse_time_range(text)
    time_value = _parse_time_value(text)
    duration = _parse_duration_minutes(text)
    if not date and time_range:
        start = dt.datetime.combine(now.date(), time_range[0])
        date = start.date() if start >= now else (now.date() + dt.timedelta(days=1))
    if not date and time_value:
        date = _resolve_date_for_time(now, None, time_value).date()
    return date, time_range, time_value, duration


def _detect_day_from_text(text: str, now: dt.datetime) -> dt.date:
    lower = text.lower()
    if "сегодня" in lower or "today" in lower:
        return now.date()
    if "послезавтра" in lower:
        return now.date() + dt.timedelta(days=2)
    if "завтра" in lower or "tomorrow" in lower:
        return now.date() + dt.timedelta(days=1)
    m = DATE_TOKEN_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = re.search(r"\b(следующ(?:ий|ая|ее)\s+)?(пн|вт|ср|чт|пт|сб|вс|понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b", lower)
    if m:
        token = m.group(2)
        target = RUS_WEEKDAY_MAP.get(token, now.weekday())
        days_ahead = (target - now.weekday() + 7) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        return now.date() + dt.timedelta(days=days_ahead)
    return now.date()
