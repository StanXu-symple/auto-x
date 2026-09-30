from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class QQPlaceholder(Base):
    __tablename__ = "qq_placeholders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    placeholder: Mapped[str] = mapped_column(String(66), unique=True)
    source_field: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
