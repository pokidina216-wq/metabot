"""
Аналитические события — агрегация по дням.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import Date, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AnalyticsEvent(Base):
    """Ежедневная аналитика — агрегированные метрики."""

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    # Возможные metric:
    #   new_users, active_users, total_requests,
    #   metadata_requests, username_requests, osint_requests,
    #   new_subscriptions, revenue_usd, revenue_stars,
    #   referral_registrations

    value: Mapped[int] = mapped_column(Integer, default=0)
    value_decimal: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)

    __table_args__ = (
        Index("ix_analytics_date_metric", "event_date", "metric", unique=True),
    )
