import datetime as dt
from sqlalchemy import String, Integer, Boolean, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class RoutineConfig(Base):
    __tablename__ = "routine_configs"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_routine_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Sleep target
    sleep_target_bedtime: Mapped[str] = mapped_column(String(5), default="23:45")
    sleep_target_wakeup: Mapped[str] = mapped_column(String(5), default="07:30")

    # Sleep hard bounds (used when generating anchors)
    sleep_latest_bedtime: Mapped[str] = mapped_column(String(5), default="01:00")
    sleep_earliest_wakeup: Mapped[str] = mapped_column(String(5), default="05:00")

    # Buffers around sleep
    pre_sleep_buffer_min: Mapped[int] = mapped_column(Integer, default=15)
    post_wake_buffer_min: Mapped[int] = mapped_column(Integer, default=45)

    # Meals
    meal_duration_min: Mapped[int] = mapped_column(Integer, default=45)
    meal_buffer_after_min: Mapped[int] = mapped_column(Integer, default=5)
    breakfast_window_start: Mapped[str] = mapped_column(String(5), default="07:00")
    breakfast_window_end: Mapped[str] = mapped_column(String(5), default="10:00")
    lunch_window_start: Mapped[str] = mapped_column(String(5), default="12:00")
    lunch_window_end: Mapped[str] = mapped_column(String(5), default="15:00")
    dinner_window_start: Mapped[str] = mapped_column(String(5), default="17:00")
    dinner_window_end: Mapped[str] = mapped_column(String(5), default="20:00")

    # Workout
    workout_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    workout_block_min: Mapped[int] = mapped_column(Integer, default=120)  # gym+shower (in-club)
    workout_travel_oneway_min: Mapped[int] = mapped_column(Integer, default=15)  # travel reserved by autoplan
    workout_start_window: Mapped[str] = mapped_column(String(5), default="06:00")
    workout_end_window: Mapped[str] = mapped_column(String(5), default="17:00")
    workout_rest_days: Mapped[int] = mapped_column(Integer, default=1)  # at least N full days between workouts
    workout_no_sunday: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow())

    user = relationship("User", back_populates="routine")
