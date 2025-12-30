from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field, field_validator


TASK_TYPE_VALUES = {"user", "anchor", "system"}
TASK_KIND_VALUES = {"meal", "workout", "morning", "work", "other"}
SCHEDULE_SOURCE_VALUES = {"manual", "autoplan", "system"}


def _validate_enum_str(value: str | None, allowed: set[str], field_name: str) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v not in allowed:
        raise ValueError(f"{field_name} must be one of: {sorted(allowed)}")
    return v


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)

    planned_start: dt.datetime | None = None
    planned_end: dt.datetime | None = None
    due_at: dt.datetime | None = None

    priority: int = Field(default=2, ge=1, le=3)
    estimate_minutes: int = Field(default=30, ge=1, le=24 * 60)

    kind: str | None = Field(default=None, description="meal|workout|morning|work|other")
    idempotency_key: str | None = Field(default=None, max_length=120)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _validate_enum_str(v, TASK_KIND_VALUES, "kind")


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=2000)

    planned_start: dt.datetime | None = None
    planned_end: dt.datetime | None = None
    due_at: dt.datetime | None = None

    priority: int | None = Field(default=None, ge=1, le=3)
    estimate_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    is_done: bool | None = None

    kind: str | None = Field(default=None, description="meal|workout|morning|work|other")

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _validate_enum_str(v, TASK_KIND_VALUES, "kind")


class TaskOut(BaseModel):
    id: int
    title: str
    notes: str | None

    task_type: str
    kind: str
    anchor_key: str | None
    schedule_source: str

    planned_start: dt.datetime | None
    planned_end: dt.datetime | None
    due_at: dt.datetime | None

    priority: int
    estimate_minutes: int
    is_done: bool

    class Config:
        from_attributes = True


class PlanOut(BaseModel):
    date: str
    scheduled: list[TaskOut]
    backlog: list[TaskOut]
