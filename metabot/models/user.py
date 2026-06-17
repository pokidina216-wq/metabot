"""
Модель пользователя — ядро системы.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    """Роли пользователей (RBAC).

    Иерархия (по возрастанию прав): USER < MODERATOR < ADMIN < OWNER.
    OWNER — единственный владелец (telegram_id == settings.owner_id); его
    права нельзя отнять (защита Owner в коде + триггер БД).
    PREMIUM/SUPERADMIN оставлены для обратной совместимости со старыми
    записями и НЕ используются в новой ролевой модели (premium → подписка,
    superadmin → owner). См. metabot/security/roles.py.
    """
    USER = "user"
    PREMIUM = "premium"        # deprecated: статус даёт подписка, не роль
    MODERATOR = "moderator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"  # deprecated: заменён на OWNER
    OWNER = "owner"


class User(TimestampMixin, Base):
    """Таблица пользователей."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Telegram данные
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Роль и статус
    # ВАЖНО: values_callable + name="user_role" — чтобы в БД хранились
    # значения enum (lowercase: "user", "admin", ...), а не имена членов
    # (uppercase: "USER"). Иначе server_default и legacy-данные не совпадут
    # с типом, и вставка ломается. См. также models/subscription.py.
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=UserRole.USER,
        server_default="user",
        nullable=False,
    )
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ban_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Защита Owner: для защищённой записи триггер БД запрещает бан, понижение
    # роли, снятие флага и удаление. Выставляется один раз для владельца.
    is_protected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Лимиты
    daily_requests_used: Mapped[int] = mapped_column(default=0, server_default="0")
    total_requests: Mapped[int] = mapped_column(default=0, server_default="0")
    last_request_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_daily_reset: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Реферальная система
    referral_code: Mapped[Optional[str]] = mapped_column(
        String(32), unique=True, nullable=True, index=True
    )
    referred_by_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    referral_bonus_balance: Mapped[int] = mapped_column(default=0, server_default="0")

    # Настройки
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    # Отношения — lazy="noload" по умолчанию, чтобы не тянуть N+1 на каждый запрос.
    # Загружайте явно через joinedload/selectinload когда нужны.
    subscriptions: Mapped[List["Subscription"]] = relationship(  # noqa: F821
        back_populates="user", lazy="noload"
    )
    payments: Mapped[List["Payment"]] = relationship(  # noqa: F821
        back_populates="user", lazy="noload"
    )
    request_logs: Mapped[List["RequestLog"]] = relationship(  # noqa: F821
        back_populates="user", lazy="noload"
    )

    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_is_banned", "is_banned"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tg={self.telegram_id} role={self.role.value}>"
