from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileOut(BaseModel):
    id: int
    telegram_chat_id: str
    full_name: str | None
    primary_focus: str | None
    preferred_language: str
    timezone: str
    is_active: bool
    onboarded: bool

    class Config:
        from_attributes = True


class ProfilePatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    primary_focus: str | None = Field(default=None, max_length=120)
    preferred_language: str | None = Field(default=None, max_length=8)
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    onboarded: bool | None = None
