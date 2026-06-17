"""
Модели администрирования — администраторы и аудит действий.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Admin(TimestampMixin, Base):
    """Таблица администраторов (дублирует роль, но хранит доп. мета)."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class AdminAction(TimestampMixin, Base):
    """Аудит-лог действий администраторов."""

    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    admin_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_admin_actions_created_at", "created_at"),
    )
