import datetime as dt
from sqlalchemy import String, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", name="uq_users_telegram_chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Almaty")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())

    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    routine = relationship("RoutineConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")
