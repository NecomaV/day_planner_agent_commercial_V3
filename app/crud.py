from __future__ import annotations

import datetime as dt
import hmac

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.checklist import TaskChecklist
from app.models.health import DailyCheckin, Habit, HabitLog
from app.models.pantry import PantryItem
from app.models.routine import RoutineConfig
from app.models.routine_step import RoutineStep
from app.models.task import Task
from app.models.reminder import Reminder
from app.models.user import User
from app.models.workout import WorkoutPlan
from app.models.usage import UsageCounter
from app.schemas.routine import RoutinePatch
from app.schemas.tasks import TaskCreate, TaskUpdate
from app.security import api_key_prefix, generate_api_key, hash_api_key
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


def get_user(db: Session, user_id: int) -> User | None:
    return db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()


def get_user_by_api_key(db: Session, raw_key: str) -> User | None:
    if not raw_key:
        return None
    prefix = api_key_prefix(raw_key)
    candidates = list(db.execute(select(User).where(User.api_key_prefix == prefix)).scalars())
    if not candidates:
        return None
    key_hash = hash_api_key(raw_key)
    for user in candidates:
        if user.api_key_hash and hmac.compare_digest(user.api_key_hash, key_hash):
            return user
    return None


def ensure_user_api_key(db: Session, user_id: int) -> str:
    user = get_user(db, user_id)
    if not user:
        raise ValueError("User not found")
    if user.api_key_hash:
        raise ValueError("User already has an API key; rotate instead")
    return rotate_user_api_key(db, user_id)


def rotate_user_api_key(db: Session, user_id: int) -> str:
    user = get_user(db, user_id)
    if not user:
        raise ValueError("User not found")
    raw_key = generate_api_key()
    user.api_key_hash = hash_api_key(raw_key)
    user.api_key_prefix = api_key_prefix(raw_key)
    user.api_key_last_rotated_at = dt.datetime.utcnow()
    user.api_key_last_used_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return raw_key


def touch_user_api_key(db: Session, user_id: int) -> None:
    user = get_user(db, user_id)
    if not user:
        return
    user.api_key_last_used_at = dt.datetime.utcnow()
    db.add(user)
    db.commit()


def update_user_fields(db: Session, user_id: int, **fields) -> User | None:
    allowed = {
        "full_name",
        "primary_focus",
        "preferred_language",
        "timezone",
        "is_active",
        "onboarded",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown user fields: {sorted(unknown)}")
    user = get_user(db, user_id)
    if not user:
        return None
    for key, value in fields.items():
        if value is not None:
            setattr(user, key, value)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_user_active(db: Session, user_id: int, is_active: bool) -> User | None:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        return None
    user.is_active = is_active
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def set_user_onboarded(db: Session, user_id: int, onboarded: bool) -> User | None:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        return None
    user.onboarded = onboarded
    db.add(user)
    db.commit()
    db.refresh(user)
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
        location_label=data.location_label,
        location_lat=data.location_lat,
        location_lon=data.location_lon,
        location_radius_m=data.location_radius_m,
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
        "location_label",
        "location_lat",
        "location_lon",
        "location_radius_m",
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
        location_label=fields.get("location_label"),
        location_lat=fields.get("location_lat"),
        location_lon=fields.get("location_lon"),
        location_radius_m=fields.get("location_radius_m"),
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
        "late_prompt_sent_at",
        "location_label",
        "location_lat",
        "location_lon",
        "location_radius_m",
        "location_reminder_sent_at",
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


def delete_all_tasks(db: Session, user_id: int) -> int:
    tasks = list(db.execute(select(Task).where(Task.user_id == user_id)).scalars())
    for task in tasks:
        db.delete(task)
    if tasks:
        db.commit()
    return len(tasks)


def delete_tasks_by_dates(db: Session, user_id: int, dates: list[dt.date]) -> int:
    if not dates:
        return 0
    date_set = set(dates)
    tasks = list(
        db.execute(
            select(Task).where(
                and_(
                    Task.user_id == user_id,
                    Task.task_type == "user",
                )
            )
        ).scalars()
    )
    to_delete = []
    for task in tasks:
        task_date = None
        if task.planned_start:
            task_date = task.planned_start.date()
        elif task.due_at:
            task_date = task.due_at.date()
        if task_date and task_date in date_set:
            to_delete.append(task)
    for task in to_delete:
        db.delete(task)
    if to_delete:
        db.commit()
    return len(to_delete)


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


def list_tasks_for_reminders(db: Session, user_id: int, now: dt.datetime, lead_minutes: int) -> list[Task]:
    end = now + dt.timedelta(minutes=lead_minutes)
    return list(
        db.execute(
            select(Task)
            .where(
                and_(
                    Task.user_id == user_id,
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


def list_tasks_with_location(db: Session, user_id: int) -> list[Task]:
    return list(
        db.execute(
            select(Task).where(
                and_(
                    Task.user_id == user_id,
                    Task.is_done.is_(False),
                    Task.location_lat.is_not(None),
                    Task.location_lon.is_not(None),
                )
            )
        ).scalars()
    )


def list_late_tasks(db: Session, user_id: int, now: dt.datetime, grace_minutes: int) -> list[Task]:
    if grace_minutes < 0:
        grace_minutes = 0
    threshold = now - dt.timedelta(minutes=grace_minutes)
    return list(
        db.execute(
            select(Task).where(
                and_(
                    Task.user_id == user_id,
                    Task.is_done.is_(False),
                    Task.planned_start.is_not(None),
                    Task.planned_start <= threshold,
                    Task.late_prompt_sent_at.is_(None),
                )
            )
        ).scalars()
    )


def update_user_location(db: Session, user_id: int, lat: float, lon: float, at: dt.datetime) -> None:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        return
    user.last_lat = lat
    user.last_lon = lon
    user.last_location_at = at
    db.add(user)
    db.commit()


def update_task_location(
    db: Session,
    user_id: int,
    task_id: int,
    lat: float,
    lon: float,
    radius_m: int | None = None,
    label: str | None = None,
) -> Task | None:
    task = db.execute(select(Task).where(and_(Task.id == task_id, Task.user_id == user_id))).scalar_one_or_none()
    if not task:
        return None
    task.location_lat = lat
    task.location_lon = lon
    if radius_m is not None:
        task.location_radius_m = radius_m
    if label is not None:
        task.location_label = label
    task.location_reminder_sent_at = None
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_daily_checkin(db: Session, user_id: int, day: dt.date) -> DailyCheckin | None:
    return db.execute(
        select(DailyCheckin).where(and_(DailyCheckin.user_id == user_id, DailyCheckin.day == day))
    ).scalar_one_or_none()


def upsert_daily_checkin(
    db: Session,
    user_id: int,
    day: dt.date,
    *,
    sleep_hours: float | None = None,
    energy_level: int | None = None,
    water_ml: int | None = None,
    notes: str | None = None,
) -> DailyCheckin:
    checkin = get_daily_checkin(db, user_id, day)
    if not checkin:
        checkin = DailyCheckin(
            user_id=user_id,
            day=day,
            sleep_hours=sleep_hours,
            energy_level=energy_level,
            water_ml=water_ml,
            notes=notes,
        )
        db.add(checkin)
        db.commit()
        db.refresh(checkin)
        return checkin
    if sleep_hours is not None:
        checkin.sleep_hours = sleep_hours
    if energy_level is not None:
        checkin.energy_level = energy_level
    if water_ml is not None:
        checkin.water_ml = water_ml
    if notes is not None:
        checkin.notes = notes
    db.add(checkin)
    db.commit()
    db.refresh(checkin)
    return checkin


def list_habits(db: Session, user_id: int, active_only: bool = True) -> list[Habit]:
    stmt = select(Habit).where(Habit.user_id == user_id)
    if active_only:
        stmt = stmt.where(Habit.is_active.is_(True))
    return list(db.execute(stmt.order_by(Habit.name.asc())).scalars())


def get_habit_by_name(db: Session, user_id: int, name: str) -> Habit | None:
    return db.execute(
        select(Habit).where(and_(Habit.user_id == user_id, Habit.name == name.strip()))
    ).scalar_one_or_none()


def get_habit(db: Session, user_id: int, habit_id: int) -> Habit | None:
    return db.execute(
        select(Habit).where(and_(Habit.user_id == user_id, Habit.id == habit_id))
    ).scalar_one_or_none()


def upsert_habit(
    db: Session,
    user_id: int,
    name: str,
    *,
    target_per_day: int | None = None,
    unit: str | None = None,
) -> Habit:
    name = name.strip()
    habit = get_habit_by_name(db, user_id, name)
    if habit:
        if target_per_day is not None:
            habit.target_per_day = target_per_day
        if unit is not None:
            habit.unit = unit
        habit.is_active = True
        db.add(habit)
        db.commit()
        db.refresh(habit)
        return habit
    habit = Habit(user_id=user_id, name=name, target_per_day=target_per_day, unit=unit, is_active=True)
    db.add(habit)
    db.commit()
    db.refresh(habit)
    return habit


def log_habit(db: Session, user_id: int, habit_id: int, day: dt.date, value: int = 1) -> HabitLog:
    log = HabitLog(user_id=user_id, habit_id=habit_id, day=day, value=value)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def sum_habit_for_day(db: Session, habit_id: int, day: dt.date) -> int:
    rows = db.execute(select(HabitLog.value).where(and_(HabitLog.habit_id == habit_id, HabitLog.day == day))).all()
    return sum(r[0] for r in rows)


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


def delete_all_routine_steps(db: Session, user_id: int) -> int:
    steps = list(db.execute(select(RoutineStep).where(RoutineStep.user_id == user_id)).scalars())
    for step in steps:
        db.delete(step)
    if steps:
        db.commit()
    return len(steps)


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



def create_reminder(
    db: Session,
    user_id: int,
    *,
    due_at: dt.datetime,
    channel: str,
    payload_json: str,
) -> Reminder:
    reminder = Reminder(
        user_id=user_id,
        due_at=due_at,
        channel=channel,
        payload_json=payload_json,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def list_due_reminders(db: Session, now: dt.datetime, limit: int = 100) -> list[Reminder]:
    stmt = (
        select(Reminder)
        .where(and_(Reminder.sent_at.is_(None), Reminder.due_at <= now))
        .order_by(Reminder.due_at.asc(), Reminder.id.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def mark_reminder_sent(db: Session, reminder: Reminder, sent_at: dt.datetime) -> None:
    reminder.sent_at = sent_at
    reminder.last_error = None
    db.add(reminder)


def record_reminder_failure(db: Session, reminder: Reminder, error: str) -> None:
    reminder.attempts = int(reminder.attempts or 0) + 1
    reminder.last_error = error[:400]
    db.add(reminder)


def get_usage_counter(db: Session, user_id: int, day: dt.date) -> UsageCounter | None:
    return db.execute(
        select(UsageCounter).where(and_(UsageCounter.user_id == user_id, UsageCounter.day == day))
    ).scalar_one_or_none()


def get_or_create_usage_counter(db: Session, user_id: int, day: dt.date) -> UsageCounter:
    counter = get_usage_counter(db, user_id, day)
    if counter:
        return counter
    counter = UsageCounter(user_id=user_id, day=day, ai_requests=0, transcribe_seconds=0)
    db.add(counter)
    db.commit()
    db.refresh(counter)
    return counter


def increment_ai_requests(db: Session, user_id: int, day: dt.date, amount: int = 1) -> UsageCounter:
    counter = get_or_create_usage_counter(db, user_id, day)
    counter.ai_requests = int(counter.ai_requests or 0) + amount
    db.add(counter)
    db.commit()
    db.refresh(counter)
    return counter


def increment_transcribe_seconds(db: Session, user_id: int, day: dt.date, seconds: int) -> UsageCounter:
    counter = get_or_create_usage_counter(db, user_id, day)
    counter.transcribe_seconds = int(counter.transcribe_seconds or 0) + seconds
    db.add(counter)
    db.commit()
    db.refresh(counter)
    return counter
