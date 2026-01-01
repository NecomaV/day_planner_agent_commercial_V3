import datetime as dt

from app.bot.parsing.time import resolve_date_ru, _extract_dates_from_text


def _weekday_date(now: dt.datetime, target: int) -> dt.date:
    days_ahead = (target - now.weekday() + 7) % 7
    days_ahead = 7 if days_ahead == 0 else days_ahead
    return now.date() + dt.timedelta(days=days_ahead)


def test_resolve_date_ru_examples():
    now = dt.datetime(2026, 1, 1, 9, 0)
    cases = [
        ("6 января", dt.date(2026, 1, 6)),
        ("шестое января", dt.date(2026, 1, 6)),
        ("третье января", dt.date(2026, 1, 3)),
        ("первое января", dt.date(2026, 1, 1)),
        ("второе января", dt.date(2026, 1, 2)),
        ("двадцать первое января", dt.date(2026, 1, 21)),
        ("31 декабря", dt.date(2026, 12, 31)),
        ("сегодня", dt.date(2026, 1, 1)),
        ("завтра", dt.date(2026, 1, 2)),
        ("послезавтра", dt.date(2026, 1, 3)),
        ("в понедельник", _weekday_date(now, 0)),
        ("в среду", _weekday_date(now, 2)),
        ("в воскресенье", _weekday_date(now, 6)),
        ("план на 10 января", dt.date(2026, 1, 10)),
        ("задачи на 15 января", dt.date(2026, 1, 15)),
        ("напомни 2 февраля", dt.date(2026, 2, 2)),
        ("встреча 7 марта", dt.date(2026, 3, 7)),
        ("запланируй на 4 апреля", dt.date(2026, 4, 4)),
        ("девятое мая", dt.date(2026, 5, 9)),
        ("тридцатое июня", dt.date(2026, 6, 30)),
    ]
    for text, expected in cases:
        assert resolve_date_ru(text, now) == expected


def test_resolve_date_year_rollover():
    now = dt.datetime(2026, 12, 30, 10, 0)
    assert resolve_date_ru("3 января", now) == dt.date(2027, 1, 3)
    assert resolve_date_ru("первое января", now) == dt.date(2027, 1, 1)


def test_extract_date_ranges():
    now = dt.datetime(2026, 1, 1, 9, 0)
    dates = _extract_dates_from_text("с 29 по 31 декабря", now)
    assert dates == [
        dt.date(2026, 12, 29),
        dt.date(2026, 12, 30),
        dt.date(2026, 12, 31),
    ]
