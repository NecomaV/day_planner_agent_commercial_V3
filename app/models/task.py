import datetime as dt
from sqlalchemy import String, DateTime, Integer, Boolean, ForeignKey, Index, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "anchor_key", name="uq_tasks_user_anchor_key"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_tasks_user_idempotency_key"),
        Index("ix_tasks_user_planned_start", "user_id", "planned_start"),
        Index("ix_tasks_reminder_sent_at", "reminder_sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Core fields
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Classification (commercial-ready: supports analytics, filtering, rules)
    # task_type: user | anchor | system
    task_type: Mapped[str] = mapped_column(String(20), default="user", index=True)
    # kind: meal | workout | morning | work | other
    kind: Mapped[str] = mapped_column(String(20), default="other", index=True)

    # Idempotency for system/telegram/API creates
    # - anchors: unique via anchor_key
    # - user-creates: optional idempotency_key (e.g., tg:<chat_id>:<message_id>)
    anchor_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Scheduling
    planned_start: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    estimate_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # Priority 1..3 (1 = high). Kept small to simplify UX; can be expanded later.
    priority: Mapped[int] = mapped_column(Integer, default=2)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # How the time was assigned: manual | autoplan | system
    schedule_source: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    reminder_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    late_prompt_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Location-based reminders
    location_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_radius_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_reminder_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Audit fields
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow())

    user = relationship("User", back_populates="tasks")
    checklist_items = relationship("TaskChecklist", back_populates="task", cascade="all, delete-orphan")
