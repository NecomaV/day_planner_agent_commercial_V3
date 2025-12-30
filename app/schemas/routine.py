from __future__ import annotations

from pydantic import BaseModel, Field


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
