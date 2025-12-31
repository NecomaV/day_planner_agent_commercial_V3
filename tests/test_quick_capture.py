import datetime as dt

from app.services.quick_capture import parse_quick_task


def test_parse_quick_task_date_time():
    now = dt.datetime(2025, 12, 30, 10, 0)
    result = parse_quick_task("добавь созвон 31 декабря в 14:30", now)
    assert result.title.lower().startswith("созвон")
    assert result.due_at == dt.datetime(2025, 12, 31, 14, 30)
