from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.routine import RoutineConfig
from app.models.task import Task
from app.models.user import User
from app.schemas.routine import RoutinePatch
from app.schemas.tasks import TaskCreate, TaskUpdate
from app.settings import settings


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min)
    end = start + dt.timedelta(days=1)
    return start, end


def get_or_create_user_by_chat_id(db: Session, chat_id: str, timezone: str = settings.TZ) -> User:
    user = db.execute(select(User).where(User.telegram_chat_id == str(chat_id))).scalar_one_or_none()
    if user:
        return user
    user = User(telegram_chat_id=str(chat_id), timezone=timezone)
    db.add(user)
    db.commit()
    db.refresh(user)
    # Ensure routine exists for new user
    ensure_routine(db, user.id)
    return user


def ensure_routine(db: Session, user_id: int) -> RoutineConfig:
    r = db.execute(select(RoutineConfig).where(RoutineConfig.user_id == user_id)).scalar_one_or_none()
    if r:
        return r
    r = RoutineConfig(user_id=user_id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def patch_routine(db: Session, user_id: int, patch: RoutinePatch) -> RoutineConfig:
    r = ensure_routine(db, user_id)
    data = patch.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def get_routine(db: Session, user_id: int) -> RoutineConfig:
    return ensure_routine(db, user_id)


def _infer_kind(title: str) -> str:
    t = (title or "").strip().lower()
    if any(x in t for x in ["workout", "gym", "training", "lift", "cardio", "run"]):
        return "workout"
    if any(x in t for x in ["breakfast", "lunch", "dinner", "meal", "eat", "food"]):
        return "meal"
    if any(x in t for x in ["morning", "wake", "wakeup", "wake-up"]):
        return "morning"
    if any(x in t for x in ["work", "dev", "code", "meeting", "project", "study"]):
        return "work"
    return "other"


def create_task(db: Session, user_id: int, data: TaskCreate) -> Task:
    # Idempotency: if client provided idempotency_key, return existing task.
    if data.idempotency_key:
        existing = db.execute(
            select(Task).where(
                and_(Task.user_id == user_id, Task.idempotency_key == data.idempotency_key)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    title = data.title.strip()
    if not title:
        raise ValueError("title must not be empty")

    kind = (data.kind or _infer_kind(title)).lower()
    task = Task(
        user_id=user_id,
        title=title,
        notes=data.notes,
        planned_start=data.planned_start,
        planned_end=data.planned_end,
        due_at=data.due_at,
        priority=data.priority,
        estimate_minutes=data.estimate_minutes,
        kind=kind,
        task_type="user",
        schedule_source="manual",
        idempotency_key=data.idempotency_key,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, user_id: int, task_id: int, patch: TaskUpdate) -> Task | None:
    data = patch.model_dump(exclude_unset=True)
    return update_task_fields(db, user_id, task_id, **data)


def update_task_fields(db: Session, user_id: int, task_id: int, **fields) -> Task | None:
    allowed = {
        "title",
        "notes",
        "planned_start",
        "planned_end",
        "due_at",
        "priority",
        "estimate_minutes",
        "kind",
        "is_done",
        "task_type",
        "anchor_key",
        "schedule_source",
        "idempotency_key",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown task fields: {sorted(unknown)}")

    task = db.execute(select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))).scalar_one_or_none()
    if not task:
        return None

    if "title" in fields and fields["title"] is not None:
        fields["title"] = fields["title"].strip()
    if "kind" in fields and fields["kind"] is not None:
        fields["kind"] = fields["kind"].lower()
    for k, v in fields.items():
        setattr(task, k, v)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, user_id: int, task_id: int) -> bool:
    task = db.execute(select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))).scalar_one_or_none()
    if not task:
        return False
    db.delete(task)
    db.commit()
    return True


def get_task(db: Session, user_id: int, task_id: int) -> Task | None:
    return db.execute(select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))).scalar_one_or_none()


def list_scheduled_for_day(db: Session, user_id: int, day: dt.date) -> list[Task]:
    start, end = _day_bounds(day)
    return list(
        db.execute(
            select(Task)
            .where(
                and_(
                    Task.user_id == user_id,
                    Task.planned_start >= start,
                    Task.planned_start < end,
                )
            )
            .order_by(Task.planned_start.asc(), Task.id.asc())
        ).scalars()
    )


def list_backlog(db: Session, user_id: int) -> list[Task]:
    return list(
        db.execute(
            select(Task)
            .where(
                and_(
                    Task.user_id == user_id,
                    Task.task_type == "user",
                    Task.is_done.is_(False),
                    Task.planned_start.is_(None),
                )
            )
            .order_by(Task.priority.asc(), Task.created_at.asc(), Task.id.asc())
        ).scalars()
    )


def list_tasks_for_day(db: Session, user_id: int, day: dt.date) -> list[Task]:
    start, end = _day_bounds(day)
    return list(
        db.execute(
            select(Task)
            .where(
                and_(
                    Task.user_id == user_id,
                    or_(
                        and_(Task.planned_start >= start, Task.planned_start < end),
                        Task.planned_start.is_(None),
                    ),
                )
            )
            .order_by(Task.planned_start.asc(), Task.priority.asc(), Task.created_at.asc(), Task.id.asc())
        ).scalars()
    )


def upsert_anchor(
    db: Session,
    user_id: int,
    anchor_key: str,
    *,
    title: str,
    kind: str,
    planned_start: dt.datetime,
    planned_end: dt.datetime,
    notes: str | None = None,
) -> Task:
    existing = db.execute(
        select(Task).where(and_(Task.user_id == user_id, Task.anchor_key == anchor_key))
    ).scalar_one_or_none()

    if existing:
        existing.title = title
        existing.kind = kind
        existing.task_type = "anchor"
        existing.schedule_source = "system"
        existing.notes = notes
        existing.planned_start = planned_start
        existing.planned_end = planned_end
        existing.priority = 1
        existing.estimate_minutes = int((planned_end - planned_start).total_seconds() // 60)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    task = Task(
        user_id=user_id,
        title=title,
        notes=notes,
        task_type="anchor",
        kind=kind,
        anchor_key=anchor_key,
        schedule_source="system",
        planned_start=planned_start,
        planned_end=planned_end,
        priority=1,
        estimate_minutes=int((planned_end - planned_start).total_seconds() // 60),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
