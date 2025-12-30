from __future__ import annotations

import datetime as dt

from app import crud
from app.services.slots import day_bounds


def _now_local_naive() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def ensure_day_routine_steps(db, user_id: int, day: dt.date, routine) -> list:
    steps = crud.list_routine_steps(db, user_id, active_only=True)
    if not steps:
        return []

    now = _now_local_naive()
    _day_start, _day_end, _morning_start, morning_end = day_bounds(day, routine, now=now)

    created = []
    base = morning_end
    for step in steps:
        offset = max(0, int(step.offset_min or 0))
        duration = max(1, int(step.duration_min or 1))
        start = base + dt.timedelta(minutes=offset)
        end = start + dt.timedelta(minutes=duration)

        task = crud.create_task_fields(
            db,
            user_id,
            title=step.title,
            notes=None,
            planned_start=start,
            planned_end=end,
            estimate_minutes=duration,
            kind=step.kind or "morning",
            task_type="system",
            schedule_source="system",
            idempotency_key=f"routine:{step.id}:{day.isoformat()}",
        )
        created.append(task)

    return created
