"""
Модели аудита и безопасности (Этап 2).

- AuditLog — append-only журнал действий (кто, что, над кем, before/after).
  Изменение/удаление строк запрещено триггером БД.
- SecurityEvent — журнал событий безопасности (отказы доступа, попытки
  тронуть Owner, входы в админку и т.п.).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, String, func, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from .base import Base


class AuditLog(Base):
    """Неизменяемый журнал действий (append-only)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Кто совершил действие (Telegram ID хранится всегда, FK — best-effort).
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Над кем/чем действие
    target_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Снимки состояния до/после
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_audit_log_action_created", "action", "created_at"),
    )


class SecurityEvent(Base):
    """Журнал событий безопасности."""

    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # info / warning / critical
    severity: Mapped[str] = mapped_column(String(16), nullable=False, server_default="info")

    actor_tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_security_events_type_created", "event_type", "created_at"),
    )
