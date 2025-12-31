import datetime as dt
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    due_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    payload_json: Mapped[str] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)

    user = relationship("User", back_populates="reminders")
