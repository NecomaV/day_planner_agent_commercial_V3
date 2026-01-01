from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.db import describe_db
from app.models.reminder import Reminder
from app.models.task import Task


def build_db_debug(db, user_id: int) -> dict:
    info = describe_db()

    total_tasks = db.execute(
        select(func.count()).select_from(Task).where(Task.user_id == user_id)
    ).scalar_one()
    backlog_tasks = db.execute(
        select(func.count())
        .select_from(Task)
        .where(
            Task.user_id == user_id,
            Task.task_type == "user",
            Task.is_done.is_(False),
            Task.planned_start.is_(None),
        )
    ).scalar_one()
    due_reminders = db.execute(
        select(func.count())
        .select_from(Reminder)
        .where(
            Reminder.user_id == user_id,
            Reminder.sent_at.is_(None),
            Reminder.due_at <= dt.datetime.utcnow(),
        )
    ).scalar_one()

    info.update(
        {
            "user_id": user_id,
            "tasks_total": int(total_tasks),
            "tasks_backlog": int(backlog_tasks),
            "reminders_due": int(due_reminders),
        }
    )
    return info
