import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class PantryItem(Base):
    __tablename__ = "pantry_items"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_pantry_user_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.utcnow(), onupdate=lambda: dt.datetime.utcnow())

    user = relationship("User", back_populates="pantry_items")
