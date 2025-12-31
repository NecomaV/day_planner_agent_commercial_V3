from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field


class CheckinIn(BaseModel):
    day: dt.date | None = None
    sleep_hours: float | None = None
    energy_level: int | None = Field(default=None, ge=1, le=5)
    water_ml: int | None = Field(default=None, ge=0, le=10000)
    notes: str | None = Field(default=None, max_length=300)


class CheckinOut(BaseModel):
    day: dt.date
    sleep_hours: float | None
    energy_level: int | None
    water_ml: int | None
    notes: str | None

    class Config:
        from_attributes = True


class HabitOut(BaseModel):
    id: int
    name: str
    target_per_day: int | None
    unit: str | None
    is_active: bool

    class Config:
        from_attributes = True


class HabitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_per_day: int | None = Field(default=None, ge=1, le=1000)
    unit: str | None = Field(default=None, max_length=32)


class HabitLogIn(BaseModel):
    day: dt.date | None = None
    value: int = Field(default=1, ge=1, le=1000)
