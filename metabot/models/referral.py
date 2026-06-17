"""
Реферальная система.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Referral(TimestampMixin, Base):
    """Связь 'кто привёл кого'."""

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Статус
    is_activated: Mapped[bool] = mapped_column(default=False, server_default="false")
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_referrals_referrer", "referrer_id"),
    )


class ReferralBonus(TimestampMixin, Base):
    """Бонусы, начисленные за рефералов."""

    __tablename__ = "referral_bonuses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    referral_id: Mapped[int] = mapped_column(
        ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False
    )

    bonus_type: Mapped[str] = mapped_column(String(32), nullable=False)  # registration / purchase
    bonus_amount: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
