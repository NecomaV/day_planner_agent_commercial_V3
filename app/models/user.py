import datetime as dt
from sqlalchemy import Boolean, String, DateTime, Integer, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", name="uq_users_telegram_chat_id"),
        UniqueConstraint("api_key_hash", name="uq_users_api_key_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    primary_focus: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(8), default="ru")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Almaty")
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_location_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(12), nullable=True)
    api_key_last_rotated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    routine = relationship("RoutineConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")
    routine_steps = relationship("RoutineStep", back_populates="user", cascade="all, delete-orphan")
    pantry_items = relationship("PantryItem", back_populates="user", cascade="all, delete-orphan")
    workout_plans = relationship("WorkoutPlan", back_populates="user", cascade="all, delete-orphan")
    daily_checkins = relationship("DailyCheckin", back_populates="user", cascade="all, delete-orphan")
    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    habit_logs = relationship("HabitLog", back_populates="user", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")
