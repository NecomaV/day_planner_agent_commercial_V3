from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


def _validate_hhmm(v: str | None) -> str | None:
    if v is None:
        return v
    if not _HHMM_RE.match(v):
        raise ValueError("time must be HH:MM")
    hh, mm = v.split(":")
    if int(hh) > 23 or int(mm) > 59:
        raise ValueError("time must be HH:MM")
    return v


class RoutineOut(BaseModel):
    sleep_target_bedtime: str
    sleep_target_wakeup: str
    sleep_latest_bedtime: str
    sleep_earliest_wakeup: str
    pre_sleep_buffer_min: int
    post_wake_buffer_min: int

    meal_duration_min: int
    meal_buffer_after_min: int
    breakfast_window_start: str
    breakfast_window_end: str
    lunch_window_start: str
    lunch_window_end: str
    dinner_window_start: str
    dinner_window_end: str

    workout_enabled: bool
    workout_block_min: int
    workout_travel_oneway_min: int
    workout_start_window: str
    workout_end_window: str
    workout_rest_days: int
    workout_no_sunday: bool

    workday_start: str
    workday_end: str
    latest_task_end: str | None
    task_buffer_after_min: int

    class Config:
        from_attributes = True


class RoutinePatch(BaseModel):
    sleep_target_bedtime: str | None = None
    sleep_target_wakeup: str | None = None
    sleep_latest_bedtime: str | None = None
    sleep_earliest_wakeup: str | None = None
    pre_sleep_buffer_min: int | None = Field(default=None, ge=0, le=120)
    post_wake_buffer_min: int | None = Field(default=None, ge=0, le=180)

    meal_duration_min: int | None = Field(default=None, ge=10, le=120)
    meal_buffer_after_min: int | None = Field(default=None, ge=0, le=60)
    breakfast_window_start: str | None = None
    breakfast_window_end: str | None = None
    lunch_window_start: str | None = None
    lunch_window_end: str | None = None
    dinner_window_start: str | None = None
    dinner_window_end: str | None = None

    workout_enabled: bool | None = None
    workout_block_min: int | None = Field(default=None, ge=30, le=240)
    workout_travel_oneway_min: int | None = Field(default=None, ge=0, le=60)
    workout_start_window: str | None = None
    workout_end_window: str | None = None
    workout_rest_days: int | None = Field(default=None, ge=0, le=7)
    workout_no_sunday: bool | None = None

    workday_start: str | None = None
    workday_end: str | None = None
    latest_task_end: str | None = None
    task_buffer_after_min: int | None = Field(default=None, ge=0, le=60)

    @field_validator(
        "sleep_target_bedtime",
        "sleep_target_wakeup",
        "sleep_latest_bedtime",
        "sleep_earliest_wakeup",
        "breakfast_window_start",
        "breakfast_window_end",
        "lunch_window_start",
        "lunch_window_end",
        "dinner_window_start",
        "dinner_window_end",
        "workout_start_window",
        "workout_end_window",
        "workday_start",
        "workday_end",
        "latest_task_end",
    )
    @classmethod
    def _time_fields(cls, v: str | None) -> str | None:
        return _validate_hhmm(v)
