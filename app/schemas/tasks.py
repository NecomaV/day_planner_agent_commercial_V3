from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field, field_validator, model_validator


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

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _validate_enum_str(v, TASK_KIND_VALUES, "kind")

    @model_validator(mode="after")
    def _validate_times(self) -> "TaskCreate":
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            raise ValueError("planned_end must be after planned_start")
        return self


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

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("title must not be empty")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _validate_enum_str(v, TASK_KIND_VALUES, "kind")

    @model_validator(mode="after")
    def _validate_times(self) -> "TaskUpdate":
        if self.planned_start and self.planned_end and self.planned_end <= self.planned_start:
            raise ValueError("planned_end must be after planned_start")
        return self


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
    reminder_sent_at: dt.datetime | None

    priority: int
    estimate_minutes: int
    is_done: bool

    class Config:
        from_attributes = True


class PlanOut(BaseModel):
    date: str
    scheduled: list[TaskOut]
    backlog: list[TaskOut]
