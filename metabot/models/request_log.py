"""
Лог запросов пользователей — для аналитики и аудита.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class RequestLog(TimestampMixin, Base):
    """Каждый значимый запрос пользователя."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # metadata_analysis / username_search / osint_query / ...
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="request_logs")  # noqa: F821

    __table_args__ = (
        Index("ix_request_logs_action", "action"),
        Index("ix_request_logs_user_created", "user_id", "created_at"),
        Index("ix_request_logs_created_at", "created_at"),
    )
