import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class RoutineStep(Base):
    __tablename__ = "routine_steps"
    __table_args__ = (
        Index("ix_routine_steps_user_pos", "user_id", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(String(200))
    offset_min: Mapped[int] = mapped_column(Integer, default=0)  # minutes after morning start
    duration_min: Mapped[int] = mapped_column(Integer, default=10)
    kind: Mapped[str] = mapped_column(String(20), default="morning")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    user = relationship("User", back_populates="routine_steps")
