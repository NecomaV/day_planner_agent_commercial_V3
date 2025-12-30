import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "weekday", name="uq_workout_user_weekday"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    weekday: Mapped[int] = mapped_column(Integer)  # 0=Mon .. 6=Sun
    title: Mapped[str] = mapped_column(String(120))
    details: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow())

    user = relationship("User", back_populates="workout_plans")
