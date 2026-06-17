"""
Кэш результатов в PostgreSQL (долгосрочный, Redis — горячий кэш).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CacheEntry(TimestampMixin, Base):
    """Долгосрочный кэш результатов (OSINT, username-проверки)."""

    __tablename__ = "cache_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    cache_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    cache_type: Mapped[str] = mapped_column(String(32), nullable=False)  # osint / username / meta
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_cache_entries_type", "cache_type"),
        Index("ix_cache_entries_expires", "expires_at"),
    )
