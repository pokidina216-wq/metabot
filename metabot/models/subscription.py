"""
Модели подписок, тарифных планов и платежей.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Index,
    Integer, Numeric, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Plan(TimestampMixin, Base):
    """Тарифные планы."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Цена
    price_usd: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    price_stars: Mapped[int] = mapped_column(Integer, default=0)

    # Длительность (дни)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    # Лимиты
    daily_request_limit: Mapped[int] = mapped_column(Integer, default=10)
    osint_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    metadata_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    username_search_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_plans_is_active", "is_active"),)


class Subscription(TimestampMixin, Base):
    """Активные подписки пользователей."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id"), nullable=False, index=True
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Отношения
    user: Mapped["User"] = relationship(back_populates="subscriptions")  # noqa: F821
    plan: Mapped["Plan"] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_subscriptions_active", "user_id", "is_active"),
        Index("ix_subscriptions_expires", "expires_at"),
    )


class SubscriptionRequestStatus(str, enum.Enum):
    """Статусы заявки на подписку (ручная монетизация)."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class SubscriptionRequest(TimestampMixin, Base):
    """Заявка на подписку. Решение принимает Owner вручную.

    Никаких автоматических выдач: пользователь оставляет заявку → Owner
    одобряет/отклоняет кнопкой → подписка активируется идемпотентно.
    """

    __tablename__ = "subscription_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)

    # values_callable + name — хранить значения (lowercase), не имена членов.
    status: Mapped[SubscriptionRequestStatus] = mapped_column(
        Enum(
            SubscriptionRequestStatus,
            name="subscription_request_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=SubscriptionRequestStatus.PENDING,
        server_default="pending",
        nullable=False,
    )

    # Кто и когда принял решение (Telegram ID Owner) + созданная подписка
    decided_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()  # noqa: F821
    plan: Mapped["Plan"] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_sub_requests_user_status", "user_id", "status"),
        Index("ix_sub_requests_status", "status"),
    )


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(TimestampMixin, Base):
    """История платежей."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True
    )

    # Сумма и способ оплаты
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)  # stars / provider
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # values_callable + name="payment_status": хранить значения enum
    # (lowercase: "pending", ...), а не имена членов. См. комментарий в
    # models/user.py — тот же класс ошибок enum.
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=PaymentStatus.PENDING,
        server_default="pending",
        nullable=False,
    )

    # Отношения
    user: Mapped["User"] = relationship(back_populates="payments")  # noqa: F821

    __table_args__ = (
        Index("ix_payments_status", "status"),
        Index("ix_payments_user_created", "user_id", "created_at"),
    )
