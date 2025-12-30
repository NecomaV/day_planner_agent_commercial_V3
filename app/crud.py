from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.checklist import TaskChecklist
from app.models.pantry import PantryItem
from app.models.routine import RoutineConfig
from app.models.routine_step import RoutineStep
from app.models.task import Task
from app.models.user import User
from app.models.workout import WorkoutPlan
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


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.id.asc())).scalars())


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


def create_task_fields(db: Session, user_id: int, **fields) -> Task:
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
        "reminder_sent_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown task fields: {sorted(unknown)}")

    title = fields.get("title")
    if title is None:
        raise ValueError("title is required")
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")

    idempotency_key = fields.get("idempotency_key")
    if idempotency_key:
        existing = db.execute(
            select(Task).where(
                and_(Task.user_id == user_id, Task.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    kind = (fields.get("kind") or _infer_kind(title)).lower()
    task_type = fields.get("task_type") or "user"
    schedule_source = fields.get("schedule_source") or "manual"
    priority = fields.get("priority") if fields.get("priority") is not None else 2
    estimate_minutes = fields.get("estimate_minutes") if fields.get("estimate_minutes") is not None else 30

    task = Task(
        user_id=user_id,
        title=title,
        notes=fields.get("notes"),
        planned_start=fields.get("planned_start"),
        planned_end=fields.get("planned_end"),
        due_at=fields.get("due_at"),
        priority=priority,
        estimate_minutes=estimate_minutes,
        kind=kind,
        task_type=task_type,
        anchor_key=fields.get("anchor_key"),
        schedule_source=schedule_source,
        idempotency_key=idempotency_key,
        is_done=fields.get("is_done", False),
        reminder_sent_at=fields.get("reminder_sent_at"),
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
        "reminder_sent_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown task fields: {sorted(unknown)}")

    task = db.execute(select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))).scalar_one_or_none()
    if not task:
        return None

    if ("planned_start" in fields or "due_at" in fields) and "reminder_sent_at" not in fields:
        fields["reminder_sent_at"] = None

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


def list_tasks_for_reminders(db: Session, now: dt.datetime, lead_minutes: int) -> list[Task]:
    end = now + dt.timedelta(minutes=lead_minutes)
    return list(
        db.execute(
            select(Task)
            .where(
                and_(
                    Task.is_done.is_(False),
                    Task.reminder_sent_at.is_(None),
                    or_(
                        and_(Task.planned_start.is_not(None), Task.planned_start >= now, Task.planned_start <= end),
                        and_(Task.due_at.is_not(None), Task.due_at >= now, Task.due_at <= end),
                    ),
                )
            )
            .order_by(Task.planned_start.asc(), Task.due_at.asc(), Task.id.asc())
        ).scalars()
    )


def add_checklist_items(db: Session, task_id: int, items: list[str]) -> list[TaskChecklist]:
    created: list[TaskChecklist] = []
    for idx, item in enumerate(items, start=1):
        text = item.strip()
        if not text:
            continue
        obj = TaskChecklist(task_id=task_id, item=text, position=idx)
        db.add(obj)
        created.append(obj)
    if created:
        db.commit()
        for obj in created:
            db.refresh(obj)
    return created


def list_checklist_items(db: Session, task_id: int) -> list[TaskChecklist]:
    return list(
        db.execute(
            select(TaskChecklist).where(TaskChecklist.task_id == task_id).order_by(TaskChecklist.position.asc())
        ).scalars()
    )


def list_routine_steps(db: Session, user_id: int, active_only: bool = True) -> list[RoutineStep]:
    query = select(RoutineStep).where(RoutineStep.user_id == user_id)
    if active_only:
        query = query.where(RoutineStep.is_active.is_(True))
    return list(
        db.execute(query.order_by(RoutineStep.position.asc(), RoutineStep.offset_min.asc(), RoutineStep.id.asc())).scalars()
    )


def add_routine_step(
    db: Session,
    user_id: int,
    title: str,
    offset_min: int,
    duration_min: int,
    kind: str,
    position: int,
) -> RoutineStep:
    step = RoutineStep(
        user_id=user_id,
        title=title.strip(),
        offset_min=offset_min,
        duration_min=duration_min,
        kind=(kind or "morning").lower(),
        position=position,
        is_active=True,
    )
    db.add(step)
    db.commit()
    db.refresh(step)
    return step


def delete_routine_step(db: Session, user_id: int, step_id: int) -> bool:
    step = db.execute(
        select(RoutineStep).where(and_(RoutineStep.user_id == user_id, RoutineStep.id == step_id))
    ).scalar_one_or_none()
    if not step:
        return False
    db.delete(step)
    db.commit()
    return True


def list_pantry_items(db: Session, user_id: int) -> list[PantryItem]:
    return list(
        db.execute(
            select(PantryItem).where(PantryItem.user_id == user_id).order_by(PantryItem.name.asc())
        ).scalars()
    )


def upsert_pantry_item(db: Session, user_id: int, name: str, quantity: str | None = None) -> PantryItem:
    item_name = name.strip().lower()
    existing = db.execute(
        select(PantryItem).where(and_(PantryItem.user_id == user_id, PantryItem.name == item_name))
    ).scalar_one_or_none()
    if existing:
        existing.quantity = quantity
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    obj = PantryItem(user_id=user_id, name=item_name, quantity=quantity)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def remove_pantry_item(db: Session, user_id: int, name: str) -> bool:
    item_name = name.strip().lower()
    existing = db.execute(
        select(PantryItem).where(and_(PantryItem.user_id == user_id, PantryItem.name == item_name))
    ).scalar_one_or_none()
    if not existing:
        return False
    db.delete(existing)
    db.commit()
    return True


def list_workout_plans(db: Session, user_id: int) -> list[WorkoutPlan]:
    return list(
        db.execute(select(WorkoutPlan).where(WorkoutPlan.user_id == user_id).order_by(WorkoutPlan.weekday.asc())).scalars()
    )


def get_workout_plan(db: Session, user_id: int, weekday: int) -> WorkoutPlan | None:
    return db.execute(
        select(WorkoutPlan).where(and_(WorkoutPlan.user_id == user_id, WorkoutPlan.weekday == weekday))
    ).scalar_one_or_none()


def set_workout_plan(db: Session, user_id: int, weekday: int, title: str, details: str | None) -> WorkoutPlan:
    existing = get_workout_plan(db, user_id, weekday)
    if existing:
        existing.title = title.strip()
        existing.details = details.strip() if details else None
        existing.is_active = True
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    plan = WorkoutPlan(
        user_id=user_id,
        weekday=weekday,
        title=title.strip(),
        details=details.strip() if details else None,
        is_active=True,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def clear_workout_plan(db: Session, user_id: int, weekday: int) -> bool:
    existing = get_workout_plan(db, user_id, weekday)
    if not existing:
        return False
    db.delete(existing)
    db.commit()
    return True


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
