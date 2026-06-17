"""
Промокоды — Owner создаёт через админ-панель.
Пользователь вводит код → получает подписку на N дней.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Index, Integer, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class PromoCode(TimestampMixin, Base):
    """Промокод на подписку."""

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Уникальный код (вводит пользователь)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    # Какой план активировать
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)

    # Сколько дней подписки даёт промокод
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # Сколько раз можно использовать (0 = безлимит)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)

    # Сколько раз уже использован
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Активен ли
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # Кто создал (Telegram ID Owner)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Срок действия (nullable = бессрочный)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Отношения
    plan: Mapped["Plan"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        Index("ix_promo_codes_active", "is_active", "code"),
    )
